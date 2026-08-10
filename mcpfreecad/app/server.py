"""CLI entrypoint and FastMCP runners for the mcpfreecad MCP server."""

import importlib.util
import base64
import logging
import os
import signal
import stat
import threading
import uuid
from urllib.parse import urljoin
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcpfreecad.bridge.client import BridgeError, FreeCADBridgeClient
from mcpfreecad.config.api_keys import match_api_key
from mcpfreecad.config.config_manager import generate_and_store_api_key, get_config, parse_arguments, reset_cached_config
from mcpfreecad.config.schema import APIKeyConfig, BridgeConfig, Config

logger = logging.getLogger(__name__)

REQUEST_STATE_KEY = "mcpfreecad_key_config"
SNAPSHOT_ROUTE_PATH = "/snapshots"
SNAPSHOT_DOWNLOAD_HREF_PREFIX = "../snapshots"


@dataclass(frozen=True)
class SnapshotRecord:
    snapshot_id: str
    path: str
    key_id: str
    access_token: str


def _freecad_mod_candidates(module_dir_name: str) -> list[Path]:
    roots = []
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    if xdg_data_home:
        roots.append(Path(xdg_data_home))
    roots.append(Path.home() / ".local" / "share")
    for root in roots:
        candidate = root / "FreeCAD" / "Mod" / module_dir_name
        if candidate.exists():
            return [candidate]
    return [root / "FreeCAD" / "Mod" / module_dir_name for root in roots]


def _a2plus_supported() -> bool:
    if importlib.util.find_spec("a2plib") is not None:
        return True
    return any(candidate.exists() for candidate in _freecad_mod_candidates("A2plus"))


@dataclass
class MCPFreeCADServerState:
    config: Config
    stdio_key: APIKeyConfig
    reload_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    snapshot_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    snapshots: dict[str, SnapshotRecord] = field(default_factory=dict, repr=False)


def _store_snapshot_record(state: MCPFreeCADServerState, path: str, key_id: str) -> SnapshotRecord:
    snapshot_id = uuid.uuid4().hex
    record = SnapshotRecord(
        snapshot_id=snapshot_id,
        path=str(Path(path).expanduser()),
        key_id=key_id,
        access_token=uuid.uuid4().hex,
    )
    with state.snapshot_lock:
        state.snapshots[snapshot_id] = record
    return record


def _get_snapshot_record(state: MCPFreeCADServerState, snapshot_id: str) -> Optional[SnapshotRecord]:
    with state.snapshot_lock:
        return state.snapshots.get(snapshot_id)


def _resolve_snapshot_file(state: MCPFreeCADServerState, snapshot_id: str, key_id: str) -> Path:
    record = _get_snapshot_record(state, snapshot_id)
    if record is None or record.key_id != key_id:
        raise RuntimeError("Snapshot not found")
    return _snapshot_record_path(record)


def _resolve_snapshot_file_by_token(state: MCPFreeCADServerState, snapshot_id: str, access_token: str) -> Path:
    record = _get_snapshot_record(state, snapshot_id)
    if record is None or record.access_token != access_token:
        raise RuntimeError("Snapshot not found")
    return _snapshot_record_path(record)


def _snapshot_record_path(record: SnapshotRecord) -> Path:
    snapshot_path = Path(record.path).expanduser()
    if not snapshot_path.exists() or not snapshot_path.is_file():
        raise RuntimeError("Snapshot file is no longer available")
    return snapshot_path


def _snapshot_base64_payload(state: MCPFreeCADServerState, snapshot_id: str, key_id: str) -> dict[str, str]:
    snapshot_path = _resolve_snapshot_file(state, snapshot_id, key_id)
    encoded = base64.b64encode(snapshot_path.read_bytes()).decode("ascii")
    return {
        "snapshot_id": snapshot_id,
        "filename": snapshot_path.name,
        "media_type": "image/png",
        "base64": encoded,
    }


def _snapshot_download_url(config: Config, snapshot_id: str, *, access_token: Optional[str] = None) -> str:
    suffix = f"{SNAPSHOT_ROUTE_PATH}/{snapshot_id}"
    if access_token:
        suffix = f"{suffix}?token={access_token}"
    remote_config = config.remote_server
    if remote_config is None or not remote_config.url_prefix:
        return f"{SNAPSHOT_DOWNLOAD_HREF_PREFIX}/{snapshot_id}" + (f"?token={access_token}" if access_token else "")
    base = remote_config.url_prefix.rstrip("/") + "/"
    return urljoin(base, suffix.lstrip("/"))


def run_mcp_server():
    args = parse_arguments()
    if args.remote and args.transport != "remotehttp":
        args.transport = "remotehttp"

    if args.genkey:
        new_key = generate_and_store_api_key(args.config, args.genkey)
        print(new_key)
        return

    config = get_config(args)
    server, state = _build_fastmcp_server(config)

    if args.transport == "remotehttp":
        _run_remote_http_transport(args, config, server, state)
        return

    _run_stdio_transport(server, state)


def _build_stdio_key_config(config: Config) -> APIKeyConfig:
    root = Path(config.stdio.library_root).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return APIKeyConfig(
        id="stdio-root",
        kdf={},
        library_root=str(root),
        allow_code_execution=config.stdio.allow_code_execution,
        allow_library_write=config.stdio.allow_library_write,
        allow_snapshots=config.stdio.allow_snapshots,
    )


def _apply_new_config_to_state(state: MCPFreeCADServerState, new_config: Config) -> None:
    state.config = new_config
    state.stdio_key = _build_stdio_key_config(new_config)


def _build_fastmcp_server(config: Config) -> tuple[FastMCP, MCPFreeCADServerState]:
    state = MCPFreeCADServerState(config=config, stdio_key=_build_stdio_key_config(config))

    @asynccontextmanager
    async def lifespan(app: FastMCP):
        yield state

    server = FastMCP(
        "mcpfreecad",
        lifespan=lifespan,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    _register_tools(server, state)
    return server, state


def _register_tools(server: FastMCP, state: MCPFreeCADServerState) -> None:
    def _key_config(ctx: Context) -> APIKeyConfig:
        request = getattr(getattr(ctx, "request_context", None), "request", None)
        if request is not None:
            key_from_request = getattr(getattr(request, "state", object()), REQUEST_STATE_KEY, None)
            if key_from_request is not None:
                return key_from_request
        return state.stdio_key

    def _bridge_client() -> FreeCADBridgeClient:
        return FreeCADBridgeClient(state.config.bridge)

    def _checked_call(ctx: Context, command: str, *, require_code=False, require_library_write=False, require_snapshots=False, **params):
        key_config = _key_config(ctx)
        if require_code and not key_config.allow_code_execution:
            raise RuntimeError("API key is not allowed to execute arbitrary code")
        if require_library_write and not key_config.allow_library_write:
            raise RuntimeError("API key is not allowed to modify the object library")
        if require_snapshots and not key_config.allow_snapshots:
            raise RuntimeError("API key is not allowed to create snapshots")
        params.setdefault("library_root", key_config.library_root)
        try:
            return _bridge_client().call(command, **params)
        except BridgeError as exc:
            raise RuntimeError(str(exc)) from exc

    @server.tool(name="bridge_status", description="Return connectivity and status details for the current FreeCAD bridge.")
    def bridge_status(ctx: Context = None):
        try:
            return _bridge_client().status()
        except BridgeError as exc:
            raise RuntimeError(str(exc)) from exc

    @server.tool(name="list_documents", description="List open FreeCAD documents and the currently active document.")
    def list_documents(ctx: Context = None):
        return _checked_call(ctx, "list_documents")

    @server.tool(name="get_document_tree", description="Inspect the current FreeCAD document/object tree, including dependency/group relationships and optional properties for planning or verification.")
    def get_document_tree(
        document_name: Optional[str] = None,
        root_name: Optional[str] = None,
        include_properties: bool = False,
        property_names: Optional[list] = None,
        max_depth: int = 4,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "get_document_tree",
            document_name=document_name,
            root_name=root_name,
            include_properties=include_properties,
            property_names=property_names,
            max_depth=max_depth,
        )

    @server.tool(name="get_object_topology", description="Inspect one object's faces, edges, and vertices so callers can choose subelements like Face1 or Edge3 for modeling or assembly constraints.")
    def get_object_topology(
        object_name: str,
        document_name: Optional[str] = None,
        include_vertices: bool = True,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "get_object_topology",
            object_name=object_name,
            document_name=document_name,
            include_vertices=include_vertices,
        )

    @server.tool(name="get_sketch_status", description="Inspect a sketch's solver state, wire closure, and basic validity before creating downstream features like pads or pockets.")
    def get_sketch_status(
        sketch_name: str,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(ctx, "get_sketch_status", sketch_name=sketch_name, document_name=document_name)

    @server.tool(name="get_sketch_details", description="Inspect the current sketch geometry and constraints so a caller can reason about what already exists.")
    def get_sketch_details(
        sketch_name: str,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(ctx, "get_sketch_details", sketch_name=sketch_name, document_name=document_name)

    @server.tool(name="add_sketch_constraint", description="Add one constraint to an existing sketch and return the new constraint index plus updated solver status.")
    def add_sketch_constraint(
        sketch_name: str,
        constraint_type: str,
        args: Optional[list] = None,
        constraint_name: Optional[str] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "add_sketch_constraint",
            sketch_name=sketch_name,
            constraint_type=constraint_type,
            args=args or [],
            constraint_name=constraint_name,
            document_name=document_name,
        )

    @server.tool(name="delete_sketch_constraint", description="Delete one constraint from an existing sketch by index or name.")
    def delete_sketch_constraint(
        sketch_name: str,
        constraint_index: Optional[int] = None,
        constraint_name: Optional[str] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "delete_sketch_constraint",
            sketch_name=sketch_name,
            constraint_index=constraint_index,
            constraint_name=constraint_name,
            document_name=document_name,
        )

    @server.tool(name="set_constraint_expression", description="Bind a sketch constraint by index or name to an expression, for example a spreadsheet alias.")
    def set_constraint_expression(
        sketch_name: str,
        expression: str,
        constraint_index: Optional[int] = None,
        constraint_name: Optional[str] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "set_constraint_expression",
            sketch_name=sketch_name,
            constraint_index=constraint_index,
            constraint_name=constraint_name,
            expression=expression,
            document_name=document_name,
        )

    @server.tool(name="rename_sketch_constraint", description="Rename a sketch constraint so callers can reference it by a stable human-readable name.")
    def rename_sketch_constraint(
        sketch_name: str,
        new_name: str,
        constraint_index: Optional[int] = None,
        constraint_name: Optional[str] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "rename_sketch_constraint",
            sketch_name=sketch_name,
            new_name=new_name,
            constraint_index=constraint_index,
            constraint_name=constraint_name,
            document_name=document_name,
        )

    @server.tool(name="dimension_rectangle_sketch", description="Add higher-level width, height, and optional anchor constraints to a rectangular sketch profile.")
    def dimension_rectangle_sketch(
        sketch_name: str,
        width: Optional[float] = None,
        height: Optional[float] = None,
        width_constraint_name: Optional[str] = None,
        height_constraint_name: Optional[str] = None,
        anchor_lower_left: bool = True,
        origin_x: Optional[float] = None,
        origin_y: Optional[float] = None,
        origin_x_constraint_name: Optional[str] = None,
        origin_y_constraint_name: Optional[str] = None,
        expressions: Optional[dict] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "dimension_rectangle_sketch",
            sketch_name=sketch_name,
            width=width,
            height=height,
            width_constraint_name=width_constraint_name,
            height_constraint_name=height_constraint_name,
            anchor_lower_left=anchor_lower_left,
            origin_x=origin_x,
            origin_y=origin_y,
            origin_x_constraint_name=origin_x_constraint_name,
            origin_y_constraint_name=origin_y_constraint_name,
            expressions=expressions or {},
            document_name=document_name,
        )

    @server.tool(name="dimension_circle_sketch", description="Add higher-level radius and optional center-anchor constraints to a circular sketch profile.")
    def dimension_circle_sketch(
        sketch_name: str,
        radius: Optional[float] = None,
        radius_constraint_name: Optional[str] = None,
        anchor_center: bool = True,
        center_x: Optional[float] = None,
        center_y: Optional[float] = None,
        center_x_constraint_name: Optional[str] = None,
        center_y_constraint_name: Optional[str] = None,
        expressions: Optional[dict] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "dimension_circle_sketch",
            sketch_name=sketch_name,
            radius=radius,
            radius_constraint_name=radius_constraint_name,
            anchor_center=anchor_center,
            center_x=center_x,
            center_y=center_y,
            center_x_constraint_name=center_x_constraint_name,
            center_y_constraint_name=center_y_constraint_name,
            expressions=expressions or {},
            document_name=document_name,
        )

    @server.tool(name="new_document", description="Create a new FreeCAD document and optionally activate it.")
    def new_document(name: Optional[str] = None, activate: bool = True, ctx: Context = None):
        return _checked_call(ctx, "new_document", name=name, activate=activate)

    @server.tool(name="open_document", description="Open an existing FreeCAD document file and optionally activate it in the running session.")
    def open_document(path: str, activate: bool = True, ctx: Context = None):
        return _checked_call(ctx, "open_document", path=path, activate=activate)

    @server.tool(name="catalog_file_objects", description="Open an existing FCStd file temporarily, list its objects, and optionally include properties or shape topology for reuse planning.")
    def catalog_file_objects(
        path: str,
        include_topology: bool = False,
        include_vertices: bool = True,
        include_properties: bool = False,
        property_names: Optional[list] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "catalog_file_objects",
            path=path,
            include_topology=include_topology,
            include_vertices=include_vertices,
            include_properties=include_properties,
            property_names=property_names,
        )

    @server.tool(name="save_document", description="Save an existing FreeCAD document, optionally to a new file path.")
    def save_document(document_name: Optional[str] = None, path: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "save_document", document_name=document_name, path=path)

    @server.tool(name="close_document", description="Close an open FreeCAD document by name.")
    def close_document(name: str, ctx: Context = None):
        return _checked_call(ctx, "close_document", name=name)

    @server.tool(name="attach_sketch_to_face", description="Attach an existing sketch to a specific face of a target object, for example before running a sketch-on-surface workflow.")
    def attach_sketch_to_face(
        sketch_name: str,
        support_object_name: str,
        face_name: str,
        map_mode: Optional[str] = "Deactivated",
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "attach_sketch_to_face",
            sketch_name=sketch_name,
            support_object_name=support_object_name,
            face_name=face_name,
            map_mode=map_mode,
            document_name=document_name,
        )

    @server.tool(name="create_sketch_on_surface", description="Create a Curves workbench Sketch On Surface feature from an attached sketch.")
    def create_sketch_on_surface(
        sketch_name: str,
        name: Optional[str] = None,
        fill_faces: bool = False,
        fill_extrusion: bool = True,
        offset: float = 0.0,
        thickness: float = 0.0,
        reverse_u: bool = False,
        reverse_v: bool = False,
        swap_uv: bool = False,
        construction_bounds: bool = True,
        extra_object_names: Optional[list[str]] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "create_sketch_on_surface",
            sketch_name=sketch_name,
            name=name,
            fill_faces=fill_faces,
            fill_extrusion=fill_extrusion,
            offset=offset,
            thickness=thickness,
            reverse_u=reverse_u,
            reverse_v=reverse_v,
            swap_uv=swap_uv,
            construction_bounds=construction_bounds,
            extra_object_names=extra_object_names or [],
            document_name=document_name,
        )

    @server.tool(name="create_body", description="Create a PartDesign body and optionally make it the active body in the current document.")
    def create_body(name: Optional[str] = None, activate: bool = True, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "create_body", name=name, activate=activate, document_name=document_name)

    @server.tool(name="set_active_body", description="Make an existing PartDesign body active in the current FreeCAD GUI document.")
    def set_active_body(body_name: str, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "set_active_body", body_name=body_name, document_name=document_name)

    @server.tool(name="set_active_document", description="Switch the active document in the running FreeCAD GUI.")
    def set_active_document(name: str, ctx: Context = None):
        return _checked_call(ctx, "set_active_document", name=name)

    @server.tool(name="execute_code", description="Execute Python code inside the current FreeCAD GUI session. Use this for custom CSG/model construction workflows.")
    def execute_code(code: str, document_name: Optional[str] = None, recompute: bool = True, ctx: Context = None):
        return _checked_call(
            ctx,
            "execute_code",
            require_code=True,
            code=code,
            document_name=document_name,
            recompute=recompute,
        )

    @server.tool(name="create_primitive", description="Create a primitive solid such as box, cylinder, sphere, cone, torus, or helix.")
    def create_primitive(kind: str, name: Optional[str] = None, params: Optional[dict] = None, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "create_primitive", kind=kind, name=name, params=params or {}, document_name=document_name)

    @server.tool(name="create_parametric_primitive", description="Create or update a mutable parametric Part primitive such as Part::Box, Part::Cylinder, Part::Sphere, Part::Cone, or Part::Torus.")
    def create_parametric_primitive(
        kind: str,
        name: Optional[str] = None,
        params: Optional[dict] = None,
        expressions: Optional[dict] = None,
        placement: Optional[dict] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "create_parametric_primitive",
            kind=kind,
            name=name,
            params=params or {},
            expressions=expressions or {},
            placement=placement,
            document_name=document_name,
        )

    @server.tool(name="create_draft_array", description="Create an orthogonal Draft array from an existing object using one, two, or three array directions.")
    def create_draft_array(
        base_object_name: str,
        vector_x: Optional[list] = None,
        vector_y: Optional[list] = None,
        count_x: int = 2,
        count_y: int = 2,
        vector_z: Optional[list] = None,
        count_z: int = 1,
        array_mode: str = "ortho",
        number: int = 5,
        angle: float = 360.0,
        center: Optional[list] = None,
        axis: Optional[list] = None,
        radial_distance: float = 100.0,
        tangential_distance: float = 50.0,
        symmetry: int = 1,
        name: Optional[str] = None,
        use_link: bool = False,
        expressions: Optional[dict] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
        ):
        return _checked_call(
            ctx,
            "create_draft_array",
            base_object_name=base_object_name,
            vector_x=vector_x,
            vector_y=vector_y,
            count_x=count_x,
            count_y=count_y,
            vector_z=vector_z,
            count_z=count_z,
            array_mode=array_mode,
            number=number,
            angle=angle,
            center=center,
            axis=axis,
            radial_distance=radial_distance,
            tangential_distance=tangential_distance,
            symmetry=symmetry,
            name=name,
            use_link=use_link,
            expressions=expressions or {},
            document_name=document_name,
        )

    if _a2plus_supported():
        @server.tool(name="import_a2plus_part", description="Import an external FCStd or STEP component into the current assembly document through the optional A2plus workbench.")
        def import_a2plus_part(
            path: str,
            name: Optional[str] = None,
            document_name: Optional[str] = None,
            extract_single_shape: bool = False,
            desired_shape_label: Optional[str] = None,
            fixed_position: Optional[bool] = None,
            ctx: Context = None,
        ):
            return _checked_call(
                ctx,
                "import_a2plus_part",
                path=path,
                name=name,
                document_name=document_name,
                extract_single_shape=extract_single_shape,
                desired_shape_label=desired_shape_label,
                fixed_position=fixed_position,
            )

        @server.tool(name="list_a2plus_parts", description="List A2plus-imported assembly components in the current document, including source file and fixed-position state.")
        def list_a2plus_parts(document_name: Optional[str] = None, ctx: Context = None):
            return _checked_call(ctx, "list_a2plus_parts", document_name=document_name)

        @server.tool(name="create_a2plus_constraint", description="Create one A2plus assembly constraint between two imported components using explicit object and subelement names.")
        def create_a2plus_constraint(
            constraint_kind: str,
            object1_name: str,
            subelement1: str,
            object2_name: str,
            subelement2: str,
            name: Optional[str] = None,
            direction_constraint: Optional[str] = None,
            offset: Optional[float] = None,
            angle: Optional[float] = None,
            lock_rotation: Optional[bool] = None,
            suppressed: Optional[bool] = None,
            solve: bool = True,
            document_name: Optional[str] = None,
            ctx: Context = None,
        ):
            return _checked_call(
                ctx,
                "create_a2plus_constraint",
                constraint_kind=constraint_kind,
                object1_name=object1_name,
                subelement1=subelement1,
                object2_name=object2_name,
                subelement2=subelement2,
                name=name,
                direction_constraint=direction_constraint,
                offset=offset,
                angle=angle,
                lock_rotation=lock_rotation,
                suppressed=suppressed,
                solve=solve,
                document_name=document_name,
            )

        @server.tool(name="list_a2plus_constraints", description="List A2plus assembly constraints in the current document, including constrained objects, subelements, and editable parameters.")
        def list_a2plus_constraints(document_name: Optional[str] = None, ctx: Context = None):
            return _checked_call(ctx, "list_a2plus_constraints", document_name=document_name)

        @server.tool(name="update_a2plus_constraint", description="Update one existing A2plus constraint's editable parameters such as offset, angle, direction, suppression, or label.")
        def update_a2plus_constraint(
            constraint_name: str,
            name: Optional[str] = None,
            direction_constraint: Optional[str] = None,
            offset: Optional[float] = None,
            angle: Optional[float] = None,
            lock_rotation: Optional[bool] = None,
            suppressed: Optional[bool] = None,
            solve: bool = True,
            document_name: Optional[str] = None,
            ctx: Context = None,
        ):
            return _checked_call(
                ctx,
                "update_a2plus_constraint",
                constraint_name=constraint_name,
                name=name,
                direction_constraint=direction_constraint,
                offset=offset,
                angle=angle,
                lock_rotation=lock_rotation,
                suppressed=suppressed,
                solve=solve,
                document_name=document_name,
            )

        @server.tool(name="delete_a2plus_constraint", description="Delete one existing A2plus constraint by object name and optionally re-solve the remaining assembly.")
        def delete_a2plus_constraint(
            constraint_name: str,
            solve: bool = True,
            document_name: Optional[str] = None,
            ctx: Context = None,
        ):
            return _checked_call(
                ctx,
                "delete_a2plus_constraint",
                constraint_name=constraint_name,
                solve=solve,
                document_name=document_name,
            )

    @server.tool(name="create_sketch", description="Create or replace a sketch from declarative geometry and optional constraints.")
    def create_sketch(
        name: str,
        geometry: list,
        constraints: Optional[list] = None,
        document_name: Optional[str] = None,
        body_name: Optional[str] = None,
        attachment_plane: Optional[str] = None,
        attachment_offset: Optional[dict] = None,
        placement: Optional[dict] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "create_sketch",
            name=name,
            geometry=geometry,
            constraints=constraints or [],
            document_name=document_name,
            body_name=body_name,
            attachment_plane=attachment_plane,
            attachment_offset=attachment_offset,
            placement=placement,
        )

    @server.tool(name="set_sketch_expression", description="Bind a sketch property or constraint path such as `Constraints[0]` to an expression, for example a spreadsheet alias.")
    def set_sketch_expression(
        sketch_name: str,
        property_path: str,
        expression: str,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "set_sketch_expression",
            sketch_name=sketch_name,
            property_path=property_path,
            expression=expression,
            document_name=document_name,
        )

    @server.tool(name="extrude_sketch", description="Create a Part::Extrusion from a sketch or 2D profile.")
    def extrude_sketch(
        sketch_name: str,
        length: float,
        name: Optional[str] = None,
        direction: Optional[list] = None,
        solid: bool = True,
        reversed: bool = False,
        symmetric: bool = False,
        document_name: Optional[str] = None,
        ctx: Context = None,
        ):
        return _checked_call(
            ctx,
            "extrude_sketch",
            sketch_name=sketch_name,
            length=length,
            name=name,
            direction=direction,
            solid=solid,
            reversed=reversed,
            symmetric=symmetric,
            document_name=document_name,
        )

    @server.tool(name="create_pad", description="Create a PartDesign Pad feature from a sketch inside a body.")
    def create_pad(
        sketch_name: str,
        length: float,
        name: Optional[str] = None,
        body_name: Optional[str] = None,
        reversed: bool = False,
        midplane: bool = False,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "create_pad",
            sketch_name=sketch_name,
            length=length,
            name=name,
            body_name=body_name,
            reversed=reversed,
            midplane=midplane,
            document_name=document_name,
        )

    @server.tool(name="create_pocket", description="Create a PartDesign Pocket feature from a sketch inside a body, for example to cut a circular hole.")
    def create_pocket(
        sketch_name: str,
        length: Optional[float] = None,
        name: Optional[str] = None,
        body_name: Optional[str] = None,
        through_all: bool = False,
        reversed: bool = False,
        midplane: bool = False,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "create_pocket",
            sketch_name=sketch_name,
            length=length,
            name=name,
            body_name=body_name,
            through_all=through_all,
            reversed=reversed,
            midplane=midplane,
            document_name=document_name,
        )

    @server.tool(name="sweep_profile", description="Sweep a sketch/profile along another sketch/path object to build pipe-like or path-driven solids.")
    def sweep_profile(
        profile_name: str,
        path_name: str,
        name: Optional[str] = None,
        solid: bool = True,
        frenet: bool = False,
        transition: Optional[str] = None,
        document_name: Optional[str] = None,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "sweep_profile",
            profile_name=profile_name,
            path_name=path_name,
            name=name,
            solid=solid,
            frenet=frenet,
            transition=transition,
            document_name=document_name,
        )

    @server.tool(name="set_spreadsheet_cells", description="Create/update a Spreadsheet workbench sheet and write one or more cells.")
    def set_spreadsheet_cells(name: str, cells: dict, document_name: Optional[str] = None, alias_map: Optional[dict] = None, ctx: Context = None):
        return _checked_call(
            ctx,
            "set_spreadsheet_cells",
            name=name,
            cells=cells,
            alias_map=alias_map or {},
            document_name=document_name,
        )

    @server.tool(name="get_spreadsheet_cells", description="Read one or more cells from a Spreadsheet workbench sheet.")
    def get_spreadsheet_cells(name: str, cells: list, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "get_spreadsheet_cells", name=name, cells=cells, document_name=document_name)

    @server.tool(name="list_helpers", description="List built-in and optional workbench-backed helpers exposed by the bridge, including availability metadata.")
    def list_helpers(ctx: Context = None):
        return _checked_call(ctx, "list_helpers")

    @server.tool(name="run_helper", description="Run a named helper, including built-in helpers plus optional workbench-backed helpers such as gears or fasteners when installed.")
    def run_helper(name: str, params: Optional[dict] = None, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "run_helper", name=name, params=params or {}, document_name=document_name)

    @server.tool(name="store_library_object", description="Export an object from FreeCAD into the reusable object library.")
    def store_library_object(
        object_name: str,
        entry_id: str,
        document_name: Optional[str] = None,
        metadata: Optional[dict] = None,
        overwrite: bool = False,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "store_library_object",
            require_library_write=True,
            object_name=object_name,
            entry_id=entry_id,
            document_name=document_name,
            metadata=metadata or {},
            overwrite=overwrite,
        )

    @server.tool(name="store_file_object_to_library", description="Open an existing FCStd file temporarily and export one object from it into the reusable object library.")
    def store_file_object_to_library(
        path: str,
        object_name: str,
        entry_id: str,
        metadata: Optional[dict] = None,
        overwrite: bool = False,
        ctx: Context = None,
    ):
        return _checked_call(
            ctx,
            "store_file_object_to_library",
            require_library_write=True,
            path=path,
            object_name=object_name,
            entry_id=entry_id,
            metadata=metadata or {},
            overwrite=overwrite,
        )

    @server.tool(name="import_library_object", description="Import a stored library object into the current document.")
    def import_library_object(entry_id: str, object_name: Optional[str] = None, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(
            ctx,
            "import_library_object",
            entry_id=entry_id,
            object_name=object_name,
            document_name=document_name,
        )

    @server.tool(name="list_library_objects", description="List reusable library objects stored on disk for the current API key.")
    def list_library_objects(ctx: Context = None):
        return _checked_call(ctx, "list_library_objects")

    @server.tool(name="capture_snapshot", description="Capture an image snapshot of the active 3D view for inspection.")
    def capture_snapshot(
        filename: Optional[str] = None,
        width: int = 1600,
        height: int = 1200,
        document_name: Optional[str] = None,
        fit_view: bool = True,
        orientation: Optional[str] = None,
        ctx: Context = None,
    ):
        result = _checked_call(
            ctx,
            "capture_snapshot",
            require_snapshots=True,
            filename=filename,
            width=width,
            height=height,
            document_name=document_name,
            fit_view=fit_view,
            orientation=orientation,
        )
        request = getattr(getattr(ctx, "request_context", None), "request", None)
        if request is None:
            return result
        key_config = _key_config(ctx)
        record = _store_snapshot_record(state, result["path"], key_config.id)
        result["snapshot_id"] = record.snapshot_id
        result["download_url"] = _snapshot_download_url(state.config, record.snapshot_id)
        result["download_url_with_token"] = _snapshot_download_url(
            state.config,
            record.snapshot_id,
            access_token=record.access_token,
        )
        return result

    @server.tool(name="get_snapshot_base64", description="Return a previously captured snapshot as a base64-encoded PNG payload.")
    def get_snapshot_base64(snapshot_id: str, ctx: Context = None):
        key_config = _key_config(ctx)
        if not key_config.allow_snapshots:
            raise RuntimeError("API key is not allowed to retrieve snapshots")
        return _snapshot_base64_payload(state, snapshot_id, key_config.id)

    @server.tool(name="fit_view", description="Zoom the active 3D view to fit the visible scene.")
    def fit_view(document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(ctx, "fit_view", require_snapshots=True, document_name=document_name)

    @server.tool(name="set_view_orientation", description="Set the active 3D view to a standard orientation such as top, front, right, or isometric.")
    def set_view_orientation(orientation: str, document_name: Optional[str] = None, fit_view: bool = False, ctx: Context = None):
        return _checked_call(
            ctx,
            "set_view_orientation",
            require_snapshots=True,
            orientation=orientation,
            document_name=document_name,
            fit_view=fit_view,
        )

    @server.tool(name="set_objects_visibility", description="Show or hide one or more document objects in the FreeCAD GUI.")
    def set_objects_visibility(object_names: list[str], visible: bool, document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(
            ctx,
            "set_objects_visibility",
            require_snapshots=True,
            object_names=object_names,
            visible=visible,
            document_name=document_name,
        )

    @server.tool(name="isolate_objects", description="Show only the named objects in the FreeCAD GUI and hide the rest.")
    def isolate_objects(object_names: list[str], document_name: Optional[str] = None, ctx: Context = None):
        return _checked_call(
            ctx,
            "isolate_objects",
            require_snapshots=True,
            object_names=object_names,
            document_name=document_name,
        )


def _run_stdio_transport(server: FastMCP, state: MCPFreeCADServerState):
    logger.info("Starting stdio MCP server with library root %s", state.stdio_key.library_root)
    server.run("stdio")


def _run_remote_http_transport(args, config: Config, server: FastMCP, state: MCPFreeCADServerState):
    remote_config = config.remote_server
    if remote_config is None:
        raise RuntimeError("remotehttp transport selected but remote_server configuration is missing")
    if not config.api_keys:
        raise RuntimeError("remotehttp transport requires at least one entry under api_keys")

    app = _build_remote_fastapi_app(server, state)

    try:
        import uvicorn
    except ImportError as exc:
        raise RuntimeError("Remote HTTP transport requires uvicorn. Install extras via 'pip install mcpfreecad[remote]'") from exc

    uvicorn_kwargs = {
        "app": app,
        "lifespan": "on",
        "log_config": None,
    }

    endpoint = remote_config.resolved_endpoint()
    if endpoint["port"] is not None:
        uvicorn_kwargs["host"] = endpoint["host"] or "0.0.0.0"
        uvicorn_kwargs["port"] = endpoint["port"]
    elif endpoint["uds"]:
        uds_path = endpoint["uds"]
        _prepare_uds_socket(uds_path)
        uvicorn_kwargs["uds"] = uds_path
    else:
        raise RuntimeError("remote_server configuration must provide either port or uds")

    _install_remote_reload_handler(args, state)
    uvicorn.run(**uvicorn_kwargs)


def _install_remote_reload_handler(args, state: MCPFreeCADServerState) -> None:
    if not hasattr(signal, "SIGHUP"):
        return

    def _handle_reload(signum, frame):
        del signum, frame
        try:
            with state.reload_lock:
                reset_cached_config()
                new_config = get_config(args)
                if new_config.remote_server is None:
                    logger.error("Reload aborted: remote_server block missing")
                    return
                if not new_config.api_keys:
                    logger.error("Reload aborted: api_keys section empty")
                    return
                _apply_new_config_to_state(state, new_config)
        except Exception:
            logger.exception("Failed to reload configuration on SIGHUP")
        else:
            logger.info("Remote configuration reload successful")

    signal.signal(signal.SIGHUP, _handle_reload)


def _build_remote_fastapi_app(server: FastMCP, state: MCPFreeCADServerState):
    if state.config.remote_server is None:
        raise RuntimeError("remote transport selected but remote_server config is missing")

    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import FileResponse
        from fastapi.responses import JSONResponse
        from starlette.middleware.base import BaseHTTPMiddleware
    except ImportError as exc:
        raise RuntimeError("Remote HTTP transport requires FastAPI. Install extras via 'pip install mcpfreecad[remote]'") from exc

    mcp_http_app = server.streamable_http_app()
    api_key_query_param = "mcp"
    legacy_query_param = "api_key"
    status_path = "/status"
    mount_path = "/mcp"

    @asynccontextmanager
    async def fastapi_lifespan(app):
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(mcp_http_app.router.lifespan_context(mcp_http_app))
            yield

    fastapi_app = FastAPI(title="mcpfreecad Remote Server", version="1.0", lifespan=fastapi_lifespan)

    class APIKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if request.url.path == status_path:
                return await call_next(request)
            if request.url.path.startswith(f"{SNAPSHOT_ROUTE_PATH}/") and request.query_params.get("token"):
                return await call_next(request)
            token = _extract_api_token(request, api_key_query_param, legacy_query_param)
            key_config = match_api_key(token, state.config.api_keys)
            if key_config is None:
                return JSONResponse({"detail": "Invalid or missing API key"}, status_code=401)
            request.state.mcpfreecad_key_config = key_config
            return await call_next(request)

    fastapi_app.add_middleware(APIKeyMiddleware)

    @fastapi_app.get(status_path)
    async def status():
        return JSONResponse({"running": True})

    @fastapi_app.get(f"{SNAPSHOT_ROUTE_PATH}/{{snapshot_id}}")
    async def download_snapshot(snapshot_id: str, request: Request):
        download_token = request.query_params.get("token")
        if download_token:
            try:
                snapshot_path = _resolve_snapshot_file_by_token(state, snapshot_id, download_token)
            except RuntimeError as exc:
                detail = str(exc)
                if detail == "Snapshot not found":
                    return JSONResponse({"detail": detail}, status_code=404)
                return JSONResponse({"detail": "Snapshot file is no longer available"}, status_code=410)
            return FileResponse(snapshot_path)
        key_config = getattr(request.state, REQUEST_STATE_KEY, None)
        if key_config is None:
            return JSONResponse({"detail": "Snapshot not found"}, status_code=404)
        try:
            snapshot_path = _resolve_snapshot_file(state, snapshot_id, key_config.id)
        except RuntimeError as exc:
            detail = str(exc)
            if detail == "Snapshot not found":
                return JSONResponse({"detail": detail}, status_code=404)
            return JSONResponse({"detail": "Snapshot file is no longer available"}, status_code=410)
        return FileResponse(snapshot_path)

    fastapi_app.mount(mount_path, mcp_http_app)
    return fastapi_app


def _extract_api_token(request, primary_query_param: str, secondary_query_param: str) -> Optional[str]:
    token = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.headers.get("x-api-key")
    if not token:
        token = request.query_params.get(primary_query_param)
    if not token:
        token = request.query_params.get(secondary_query_param)
    return token


def _prepare_uds_socket(path: str):
    directory = os.path.dirname(path)
    if directory and not os.path.exists(directory):
        os.makedirs(directory, exist_ok=True)
    if os.path.exists(path):
        current_stat = os.stat(path)
        if stat.S_ISSOCK(current_stat.st_mode):
            os.remove(path)
        else:
            raise RuntimeError(f"UDS path {path} exists and is not a socket")


__all__ = ["run_mcp_server", "_build_stdio_key_config", "_extract_api_token"]
