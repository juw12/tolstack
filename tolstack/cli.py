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
    paths = sub.add_parser("paths", help="list the candidate chains between the config's from/to faces")
    paths.add_argument("config")
    demo = sub.add_parser("demo", help="write the demo STEP + 2D drawings into a folder")
    demo.add_argument("folder", nargs="?", default="tolstack_demo")
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
        print(f"{len(model.contacts)} contacts found along {args.axis} (contact tolerance {model.contact_tol} mm)")
    elif args.cmd == "demo":
        from .demo import write_demo

        print(f"demo written: {write_demo(Path(args.folder))}")
    elif args.cmd == "paths":
        from .chain import find_paths
        from .pipeline import find_face, find_part, load_model, validate_config

        config_path = Path(args.config)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        validate_config(config)
        step = Path(config["step"])
        if not step.is_absolute():
            step = config_path.parent / step
        model = load_model(str(step), config.get("axis", "z"), float(config.get("contact_tol", 0.02)))
        f, t = config["from"], config["to"]
        start = find_face(model, f["part"], f.get("face", "top"), f.get("level"))
        end = find_face(model, t["part"], t.get("face", "top"), t.get("level"))
        exclude = {find_part(model, q).index for q in config.get("exclude", [])}
        options = find_paths(start.part, end.part, model.contacts, exclude - {start.part, end.part})
        if not options:
            print("no chain found - check the axis, contact_tol or exclude list")
        for i, option in enumerate(options, start=1):
            print(f"chain_option {i}: {option.describe(model.parts)}")
    else:
        from .pipeline import run_config

        config_path = Path(args.config)
        config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        try:
            contributors, result, out = run_config(config, config_path.parent)
        except (ValueError, FileNotFoundError) as err:
            raise SystemExit(f"tolstack: {err}")
        for c in contributors:
            sign = "+" if c.direction > 0 else "-"
            print(f"{c.label:>3} {sign} {c.nominal:8.3f} +{c.tol_plus:.3f}/-{c.tol_minus:.3f}  {c.part}  [{c.source}]")
        print(f"nominal {result.nominal:.3f} | WC {result.wc_min:.3f} ~ {result.wc_max:.3f} | "
              f"RSS {result.rss_min:.3f} ~ {result.rss_max:.3f} | RSS {result.verdict('rss')}")
        if result.cpk is not None:
            cp = "-" if result.cp is None else f"{result.cp:.2f}"
            print(f"Cp {cp} | Cpk {result.cpk:.2f} | predicted {result.ppm:.1f} ppm out of spec | "
                  f"Monte Carlo {result.mc_out_pct:.3f} % out")
        print(f"Excel: {out}")


if __name__ == "__main__":
    main()
