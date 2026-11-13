"""tolstack - 1-D tolerance stack-up (gap / step) from a STEP assembly and 2D drawings, to Excel."""
from .stack import Contributor, Result, analyze

__all__ = ["Contributor", "Result", "analyze"]
__version__ = "0.7.0"
