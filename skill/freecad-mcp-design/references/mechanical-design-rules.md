# Mechanical Design and Validation Rules

This file supplements the MCP tool reference with project-specific mechanical-design policy.

All checks in this file are expectation-driven. When a correction is made, rerun the affected verification until the requirement is actually matched or an explicit compromise is recorded.

## Fits and tolerances

Use a default 0.4 mm total gap for ordinary sliced or printed play-free fits unless the user supplies a process-specific value. Interpret this as a starting assumption, not a replacement for engineering judgment.

Explicitly distinguish:

- rotating clearance
- sliding clearance
- static locating fit
- press fit
- tapped hole
- screw clearance hole
- bearing seat
- nut trap
- printed-in-place gap

Long sliding interfaces, rough printers, flexible parts, and misaligned assemblies often require more clearance.

For machined or assembled metal/plastic parts, do not reuse the printed-fit default blindly. Instead classify the feature first:

- free clearance
- controlled sliding fit
- locating fit
- bearing seat
- press/interference fit
- thread-preparation feature

Then preserve the needed allowance and datum strategy for that class.

For fit-critical or thread-critical geometry, also record:

- whether the current modeled geometry represents rough, pre-finish, or final condition
- which faces or diameters must remain datums through later manufacturing steps
- whether stock must intentionally remain for reaming, boring, grinding, or thread generation

Do not leave downstream planning to infer those distinctions from nominal geometry alone.

For mating interfaces, also record:

- which part owns the locating datum
- which part is easier to replace or rework
- whether the tolerance should be held on the shaft, the bore, or both
- what inspection or assembly check will confirm the interface is acceptable

Tie that check to the owning part or interface so failed validation results have a clear place to feed back.

For standard purchased parts, also record the installation path:

- whether the part slides on, presses in, drops in before closure, snaps in, or is retained by a separate cap or clip
- which direction the part is inserted from
- what geometry remains open during assembly

Do not accept a bearing seat, insert pocket, or captured hardware cavity that shows the final position but gives no real way to place the part there.

For bearings, verify an actual locating bore and its axial engagement length,
not merely clearance to the housing. Missing support material can make every
collision check pass. Identify both axial stops, quantify permitted end float,
and check displacement toward each stop; distinguish inner- and outer-race
retention so a stationary retainer does not clamp the rotating race or seals.

## Self-aligning joints

Provide alignment helpers for every lid and joining interface, not only joints
that explicitly request alignment. Lids require a mating lip/recess or
tongue/groove; other joints require suitable pilots, locating shoulders, tapered
pins, keys, or equivalent guided geometry. This applies to push-on, snap-fit,
screwed, and gasketed joints alike. Fastener clearance holes alone are not
locators. If a functional constraint prevents this, obtain an explicit exception
rather than silently omitting alignment.

Separate initial capture from final location: use a chamfer, radius, or tapered
entry with enough initial play to accept modest lateral/angular misalignment,
then a locating land with the process-appropriate final clearance. Do not leave
the final joint loose merely to make initial insertion easy. Lips must be joined
to their owning part and engage the mating recess before final seating.

Keep guides compatible with insertion/removal direction, fastening access, and
required motion. For gasketed joints, guide the parts before significant seal
compression, avoid pinching or dragging the gasket, and preserve the specified
compression and seating stops. Alignment features must not bottom out early or
prevent the faces from seating; avoid redundant tight locators that bind.

Validate the lead-in, engagement depth, final fit, and seating with assembly
pose sweeps, including modest lateral/angular offsets within the declared
capture range and manufacturing tolerances. A nominal coaxial insertion or
collision-free final pose does not prove self-alignment. Geometry checks must
show a feasible guided path; do not claim simulated contact-driven centering
unless a contact solver actually established it. Revalidate after correction.

## Fasteners

Unless instructed otherwise:

- metric fasteners
- DIN 934 hex nuts
- DIN 912 socket-head cap screws
- DIN 7991 countersunk screws when a flush head is needed

Keep detailed threads out of early iterations. Use threadless nominal envelopes or simple cylinders during layout and interference work.

Check:

- head clearance
- washer/nut seating
- hex-key or screwdriver approach
- socket outside diameter where relevant
- screw insertion direction
- nut insertion and anti-rotation
- available tightening stroke
- bottoming depth
- whether the screw can be removed after assembly

Validate countersinks and counterbores against the selected fastener's actual
head profile, including cylindrical rims and under-head transitions, not an
idealized cone alone. Check both seated clearance/contact and tool approach;
changing screw standard or length requires rechecking the joint.

## Motion

For each movable component identify:

- intended degree(s) of freedom
- allowed range
- hard stops
- minimum clearance throughout motion
- cable or fastener constraints
- neighboring swept volumes

Validate critical poses or construct a swept envelope. A correct neutral pose alone is insufficient.

Also verify the full movement path:

- no solid material intrudes into the swept path
- no fastener head, clamp, cover, cable route, or service feature blocks the motion mid-stroke
- the intended stop positions are reachable without hidden interference
- the mechanism does not require impossible elastic deformation or assembly-only clearance to move

For driven motion, also verify engagement continuity:

- gears keep the intended mesh or backlash relationship across the operating range
- racks and pinions remain engaged where the mechanism is supposed to transmit motion
- lead screws, nuts, and followers remain on the intended driving path
- belts/chains/pulleys/couplers stay aligned enough to plausibly transmit motion
- cams, rollers, and mating contact features actually meet where the design expects actuation or support

Do not accept “looks aligned” in one pose as proof that the mechanism works.

Do not accept demonstrator-grade approximations for requirement-critical contact geometry.

If the mechanism depends on a specific profile or law of contact, for example cycloidal drive disks, involute or non-involute gear teeth, cam tracks, ratchet forms, screw/nut geometry, or sealing interfaces, verify the real intended geometry rather than a rough stand-in that only shows the general concept.

### Coupled Motion And Special Transmission Validation

For a mechanism whose function depends on coupled positions, create a
machine-readable pose manifest before calling it functional. Each pose must
name the driving coordinate, dependent component transforms, expected contact
interfaces, allowed clearances/backlash, forbidden intersections, and the
requirement it proves. Use non-destructive temporary copies or restore every
placement after each check; never leave the nominal assembly in a test pose.

Use `validate_pose_sweep` for numerical proof. A manifest contains named
`poses`, each with only the moving-object transforms for that pose; a transform
can contain `translation` and `rotation` (`axis`, `angle_deg`, and `origin`).
Always give a rotation its actual joint, shaft, or assembly pivot as `origin`;
the global origin is not a safe default for arbitrary mechanisms. Declare
individual `interfaces` as `allowed_contact` or `forbidden_clearance`, and use
`contact_groups` when one moving profile must engage a minimum count of valid
candidates such as ring pins. A candidate counts as engaged only within the
declared contact gap and permitted interference. Record the manifest, extrema,
and every reported failing pose; revise the model and rerun until the required
result passes.

The current bridge limits each request to 4096 pair evaluations: pose count
times (individual interfaces plus moving/candidate pair counts in contact
groups). Split larger sweeps into traceable batches without dropping required
poses or interfaces; keep each contact group's complete candidate set. Exact
geometry checks can take many minutes. On a timeout, poll `get_bridge_progress`
and retain the request identity; do not retry or queue dependent work while it
is still running. A completed RPC without a retrieved validation result is
not a pass. Sweep each rotating component's full operating range: one input
revolution may cover only a fraction of a geared output's travel.

Use `contained_in` for a moving solid that must remain inside a positive
envelope such as a carrier pin in a disk hole/slot, a bearing in a retaining
pocket, or an inserted component along its assembly path. The first object is
the contained solid, the second is the permitted cavity/envelope, and the
default required containment ratio is 100 percent. Do not use a mere
clearance-to-hole-wall measurement as proof that a pin has not escaped.
Conversely, containment and non-interference do not prove drive engagement.
For every load-transmitting interface, also check the intended mating surfaces,
their dimensions and maximum working gap; prescribing dependent poses cannot
make an oversized bore or an undersized journal transmit motion.

Sweep the full operating cycle at enough samples to catch loss of contact
(normally at least 36 evenly distributed input phases, plus analytically or
geometrically identified contact-transition phases). Refine any interval whose
clearance, contact count, or penetration changes rapidly. At every sampled
pose, verify:

- the declared input-to-output and intermediate motion law is applied, not an
  independently guessed placement
- only explicitly allowed interfaces contact; all other parts remain clear
- intended driving/contact pairs remain within their specified contact,
  backlash, or rolling-clearance band
- no allowed contact exceeds its permitted interference/pressure-fit envelope
- no moving body crosses a housing, fastener, bearing, cable route, fixture,
  or protected static part
- all torque-transfer features remain engaged and retain the intended degrees
  of freedom

For a cycloidal reducer specifically, the manifest must identify the ring-pin
count, disk-lobe count, pin radius, eccentricity, disk profile law, input to
disk/output phase relationship, disk axial planes, and the intended backlash
or clearance. Validate both disks through the same input cycle with their
eccentric centers exactly 180 degrees opposed. For each disk and phase,
validate ring-pin engagement against the actual rolling-locus profile, not its
bounding radius or nominal pin circle.

Also model and validate the output-carrier coupling. Each carrier pin must
remain inside its assigned disk output hole/slot through the complete
eccentric orbit, have the clearance needed for that relative motion, transmit
the intended output constraint, and not collide with the disk body. A static
carrier hub that merely overlaps two disks, or pins at nominal positions with
no full-cycle engagement proof, is a failure. Check the two disk phases and
carrier pin pattern together; do not validate each disk in isolation.

Record numerical min/max clearance, contact/engagement count, maximum allowed
interference, and the failing pose IDs. Capture and inspect screenshots of
neutral, peak-positive eccentric, peak-negative eccentric, and every failing
or near-limit pose. A valid neutral pose, one screenshot, nominal pin-circle
alignment, or a non-intersecting static assembly is not a mechanism proof.

Phrases such as:

- “good enough as a demonstrator”
- “close enough for now”
- “expect more backlash”
- “the pins are right even if the profile is approximate”

are not validation. Treat them as unresolved blockers unless the user explicitly asked for a concept-only model or explicitly accepted the degraded behavior.

## Holes and passages

For every hole or channel:

- verify why it exists
- verify the cut reaches the intended destination
- verify no later union or feature seals it
- inspect along its axis when possible
- distinguish blind and through holes
- verify counterbores/countersinks are on the intended side
- verify tool and fastener access

Do not tolerate leftover editing holes.

Every drilled hole should have an explicit purpose, for example:

- fastener clearance
- tap-drill preparation
- dowel or locating feature
- bearing lubrication or service access
- cable, tube, or hose routing
- drainage or venting
- inspection or adjustment access
- intentional weight reduction with a stated structural/manufacturing reason

If a drilled hole does not have a current purpose, remove it or record the real reason explicitly.

After iterative edits, specifically check for:

- duplicate holes left from earlier feature patterns
- holes that no longer align with mating parts or fasteners
- abandoned access holes from a superseded assembly concept
- cut features that still pass through the body even though the requirement moved elsewhere

Treat cable passages as first-class channels, not as an afterthought.

For every required cable route:

- verify the route reaches its intended internal component or exits to the outside when that is the requirement
- verify no later feature, cover, clamp, boss, bearing seat, or fastener pocket blocks or crushes the route
- verify bends, cross-sections, and entry/exit openings are plausible for installation and service rather than only geometrically connected
- verify the route stays compatible with moving parts, travel envelopes, and assembly order
- treat required tubes, hoses, and similar service lines with the same level of importance

Also classify the manufacturing intent:

- clearance hole
- tap-drill hole
- threaded hole
- reamed/dowel hole
- bearing or locating bore

Nominal diameter alone is not enough for a mechanically correct design handoff.

For threaded features, make the intended manufacturing role explicit:

- clearance for purchased fastener
- tap-drill only
- modeled thread for manufactured mating part
- insert, helical insert, or threaded bushing seat

Do not imply a manufactured thread merely by drawing a helical-looking feature.

## Downstream manufacturability handoff

Before considering a design ready for process planning or CAM, make sure the model or accompanying notes make these points explicit where relevant:

- fit type
- intended datum surfaces
- rough-vs-finish status of fit-critical features
- whether a later process must leave or remove remaining stock
- whether modeled threads are real manufacturing intent or only visual placeholders
- whether required cable, tube, or hose routes are internal, external, or interface-crossing and how they remain open through assembly
- whether any remaining simplified geometry is truly non-critical placeholder geometry or an unresolved substitute for a requirement-critical working profile

If any of these are still unclear after inspection, revise the model or notes and recheck. Do not hand off ambiguous manufacturing intent and assume downstream planning will guess correctly.

When the handoff is still failing the intended manufacturing interpretation, iterate on the model, naming, or notes and recheck until the feature intent is actually unambiguous or the unresolved ambiguity is explicitly recorded as a risk.

## Assemblability

Construct an explicit assembly sequence mentally or in notes. Reject geometries that require impossible insertion, simultaneous incompatible motions, or tools that cannot reach the fastener.

Apply this explicitly to bearings and other purchased parts:

- bearings must be able to slide onto shafts or into housings from a real assembly direction
- retained bearings should have a plausible cap, clip, shoulder, split housing, or staged assembly sequence
- do not bury a bearing inside a monolithic printed or machined part unless the design is intentionally split so the bearing can be installed before closure
- the same rule applies to bushings, nuts, threaded inserts, seals, and other captured standard components

When an assembly or access check fails, change the smallest responsible feature, interface, or sequence assumption and rerun the relevant checks until the requirement is satisfied or the compromise is explicitly accepted.

The same rerun-until-pass rule applies to fit checks, hole-open checks, datum preservation, and tool-access checks. "Probably machinable" is not a completed design state.

It also applies to hole-purpose checks. A body with unexplained leftover holes is not a validated design state.

It also applies to profile-correctness checks. A transmission or contact feature that is only approximately shaped is not validated merely because the surrounding reference points are in the right places.

## Expensive operations

Detailed gears and threaded screws can make intersections and booleans take minutes or hours.

Prefer:

- analytical geometry
- pitch cylinders
- tooth-tip/root envelopes
- threadless fastener proxies
- local or selective checks
- late replacement of proxies with detailed helpers

External gears may behave poorly in booleans. Prefer negative/internal tooth-space cutting tools where that gives a more robust construction.
