#!/bin/bash
# Run this from the root of the cpg-platform project to apply all fixes
# Usage: bash fix_files.sh

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Applying fixes from: $SCRIPT_DIR"

# trainer.py - remove prophet, fix numpy ints
python3 - << 'PYEOF'
import pathlib, os

script_dir = os.environ.get('SCRIPT_DIR', '.')
files = {
    'backend/app/forecasting/training/trainer.py': [
        ('model_names = model_names or ["prophet", "lightgbm"]',
         'model_names = [m for m in (model_names or ["lightgbm"]) if m != "prophet"]'),
        ("model_names = model_names or ['prophet', 'lightgbm']",
         "model_names = [m for m in (model_names or ['lightgbm']) if m != 'prophet']"),
        ('for cat_id, reg_id in segments:\n        total += 1',
         'for cat_id, reg_id in segments:\n        cat_id = int(cat_id) if cat_id is not None else None\n        reg_id = int(reg_id) if reg_id is not None else None\n        total += 1'),
        ('for model_name in model_names:\n        log.info("training.model_start"',
         'for model_name in [m for m in model_names if m != "prophet"]:\n        log.info("training.model_start"'),
    ],
    'backend/app/forecasting/pipeline/predictor.py': [
        ('for cat_id, reg_id in segments:\n        try:\n            result = predict_segment',
         'for cat_id, reg_id in segments:\n        cat_id = int(cat_id) if cat_id is not None else None\n        reg_id = int(reg_id) if reg_id is not None else None\n        try:\n            result = predict_segment'),
    ],
    'backend/app/schemas/forecasting.py': [
        ('"prophet", "lightgbm"', '"lightgbm"'),
        ("'prophet', 'lightgbm'", "'lightgbm'"),
        ('{"prophet", "lightgbm"}', '{"lightgbm"}'),
    ],
}

for filepath, replacements in files.items():
    p = pathlib.Path(filepath)
    if not p.exists():
        print(f'SKIP (not found): {filepath}')
        continue
    t = p.read_text()
    changed = False
    for old, new in replacements:
        if old in t:
            t = t.replace(old, new)
            changed = True
    if changed:
        p.write_text(t)
        print(f'FIXED: {filepath}')
    else:
        print(f'OK (no change needed): {filepath}')
PYEOF

echo ""
echo "Verify trainer.py:"
grep "model_names or\|model_names =" backend/app/forecasting/training/trainer.py | head -3

echo ""
echo "Done! Now run:"
echo "  docker compose down"
echo "  docker compose build --no-cache api"
echo "  docker compose up -d"
echo "  python backend/scripts/generate_synthetic_data.py --train --forecast"
