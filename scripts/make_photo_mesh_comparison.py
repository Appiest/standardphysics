"""Native-size comparison page for benchmark runs.

Embeds the frozen-view 500 x 500 PNGs at exactly 100 percent CSS size with the
machine metrics table. Never enlarge or interpolate images in CSS.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evaluations", nargs="+", type=Path, required=True,
                        help="evaluation.json files in comparison order")
    parser.add_argument("--labels", nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if len(args.evaluations) != len(args.labels):
        raise SystemExit("need one label per evaluation")
    evaluations = [json.loads(p.read_text()) for p in args.evaluations]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    import shutil
    row_dir = args.out.parent / "images"
    row_dir.mkdir(exist_ok=True)
    rows = []
    first = evaluations[0]
    for view in first["views"]:
        cells = []
        for evaluation, label in zip(evaluations, args.labels, strict=True):
            matching = [v for v in evaluation["views"] if v["id"] == view["id"]]
            entry = matching[0] if matching else {}
            ref = entry.get("passes", {}).get("rgb")
            if ref:
                if "-rgb-500.png" in str(ref):
                    src_file = Path(ref)
                    rel = f"images/{label}-{view['id']}-rgb.png"
                else:
                    src_path = Path(ref)
                    rgb500 = src_path.parent / f"{view['id']}-rgb-500.png"
                    src_file = rgb500 if rgb500.is_file() else src_path
                    rel = f"images/{label}-{view['id']}-rgb.png"
                shutil.copy(src_file, row_dir / f"{label}-{view['id']}-rgb.png")
            else:
                rel = ""
            cells.append(f"<img src='{rel}' width='500' height='500' alt='{label} {view['id']}'>")
        metrics = []
        for evaluation, label in zip(evaluations, args.labels, strict=True):
            entry = [v for v in evaluation["views"] if v["id"] == view["id"]][0]
            coverage = entry.get("coverage", {})
            psnr = entry.get("psnr_ssim", {})
            fm = entry.get("feature_matches", {})
            metrics.append(
                f"<tr><td>{label}</td>"
                f"<td>{coverage.get('photographed_fraction', 'n/a')}</td>"
                f"<td>{psnr.get('psnr', 'n/a')}</td>"
                f"<td>{psnr.get('ssim', 'n/a')}</td>"
                f"<td>{fm.get('matches', 'n/a')}</td>"
                f"<td>{fm.get('median_error_px', 'n/a')}</td></tr>")
        reference_cell = "<span>no photographic reference (novel view)</span>"
        if view.get("reference"):
            first_eval_dir = Path(args.evaluations[0]).parent
            ref_file = first_eval_dir / f"{view['id']}-reference-500.png"
            if ref_file.is_file():
                shutil.copy(ref_file, row_dir / ref_file.name)
                reference_cell = f"<img src='images/{ref_file.name}' width='500' height='500' alt='reference'>"
        rows.append(
            f"<h2>{view['id']} ({view['kind']})</h2>"
            f"<div class='strip'>{reference_cell}{''.join(cells)}</div>"
            f"<table><tr><th>config</th><th>photographed fraction</th><th>psnr</th><th>ssim</th>"
            f"<th>sift matches</th><th>median error px</th></tr>{''.join(metrics)}</table>")
    page = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>photo-mesh-500 comparison</title>
<style>
 body {{ font-family: -apple-system, sans-serif; margin: 24px; background: #f4f3f1; }}
 h1 {{ font-size: 22px; }} h2 {{ font-size: 15px; margin-top: 28px; }}
 .strip {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-start; }}
 .strip span {{ color: #666; }}
 img {{ image-rendering: auto; border: 1px solid #bbb; background: #fff; }}
 table {{ border-collapse: collapse; margin-top: 8px; font-size: 12px; }}
 td, th {{ border: 1px solid #ccc; padding: 3px 10px; text-align: right; }}
 th {{ background: #e8e6e2; }}
</style></head><body>
<h1>photo-mesh-500 comparison (native 500 x 500 display, no CSS enlargement)</h1>
<p>Machine metrics. Perceptual and user review are separate gates and pending.</p>
{''.join(rows)}
</body></html>"""
    args.out.write_text(page)
    print(args.out, len(rows), "views")


if __name__ == "__main__":
    main()
