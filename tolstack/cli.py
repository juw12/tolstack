"""Command line: `tolstack faces model.step` and `tolstack run config.yaml`."""
from __future__ import annotations

import argparse
from pathlib import Path

import yaml


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tolstack", description="1-D tolerance stack-up from STEP + 2D")
    sub = parser.add_subparsers(dest="cmd", required=True)
    faces = sub.add_parser("faces", help="list parts and their planar faces along an axis")
    faces.add_argument("step")
    faces.add_argument("--axis", default="z", choices=["x", "y", "z"])
    faces.add_argument("--part", help="only parts whose name contains this text")
    run = sub.add_parser("run", help="run an analysis from a YAML config and write Excel")
    run.add_argument("config")
    args = parser.parse_args(argv)

    if args.cmd == "faces":
        from .pipeline import load_model

        model = load_model(args.step, args.axis)
        for part in model.parts:
            if args.part and args.part.lower() not in part.name.lower():
                continue
            print(f"[{part.index}] {part.name}")
            for f in model.faces_of(part.index):
                print(f"     {f.kind:6s} @ {f.level:10.4f}   area {f.area:9.2f}")
        print(f"{len(model.contacts)} contacts found along {args.axis}")
    else:
        from .pipeline import run_config

        config_path = Path(args.config)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        contributors, result, out = run_config(config, config_path.parent)
        for c in contributors:
            sign = "+" if c.direction > 0 else "-"
            print(f"{c.label:>3} {sign} {c.nominal:8.3f} +{c.tol_plus:.3f}/-{c.tol_minus:.3f}  {c.part}  [{c.source}]")
        print(f"nominal {result.nominal:.3f} | WC {result.wc_min:.3f} ~ {result.wc_max:.3f} | "
              f"RSS {result.rss_min:.3f} ~ {result.rss_max:.3f} | RSS {result.verdict('rss')}")
        print(f"Excel: {out}")


if __name__ == "__main__":
    main()
