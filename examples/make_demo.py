"""Create the demo assembly (STEP) and matching 2D drawings (PDF) in examples/demo/.

    python examples/make_demo.py        (same as: python -m tolstack demo examples/demo)

Stack along Z:  base (pocket at z=3) -> post (3..19)   and   base (top z=5) -> spacer -> pcb -> cap
Step to check:  cap top (20.6) vs post top (19.0)  = +1.6 nominal
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run without installing

from tolstack.demo import box, demo_parts, write_demo, write_drawing, write_step  # noqa: F401

HERE = Path(__file__).parent


def main():
    step = write_demo(HERE / "demo")
    print(f"demo written to {step.parent}")


if __name__ == "__main__":
    main()
