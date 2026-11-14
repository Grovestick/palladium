"""Where Handler's parts (pd_h_*.py) find the server: pd-server.py puts its own module here
before it imports them, so a part reads the server's names as its class is made too -
a default argument is worked out then, not when the method is called."""
from typing import Any

#: pd-server.py's module
SERVER: Any = None
