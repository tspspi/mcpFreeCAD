# Mechanical Design and Validation Rules

This file supplements the MCP tool reference with project-specific mechanical-design policy.

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

## Motion

For each movable component identify:

- intended degree(s) of freedom
- allowed range
- hard stops
- minimum clearance throughout motion
- cable or fastener constraints
- neighboring swept volumes

Validate critical poses or construct a swept envelope. A correct neutral pose alone is insufficient.

## Holes and passages

For every hole or channel:

- verify the cut reaches the intended destination
- verify no later union or feature seals it
- inspect along its axis when possible
- distinguish blind and through holes
- verify counterbores/countersinks are on the intended side
- verify tool and fastener access

## Assemblability

Construct an explicit assembly sequence mentally or in notes. Reject geometries that require impossible insertion, simultaneous incompatible motions, or tools that cannot reach the fastener.

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
