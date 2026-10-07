"""Stand-ins for the services bombadil-connect talks to: each runs in the test's own event loop on a localhost
port, so a driver is tested against something that speaks the real wire (HTTP, WebSocket, JSON) and nothing else."""
