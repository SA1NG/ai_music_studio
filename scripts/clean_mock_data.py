"""Xóa dữ liệu mock khỏi DB và storage (hỏi xác nhận trước).

    python scripts/clean_mock_data.py            # in danh sách, hỏi 'yes' rồi mới xóa
    python scripts/clean_mock_data.py --dry-run  # chỉ in danh sách
    python scripts/clean_mock_data.py --yes      # không hỏi (dùng trong script)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from api.cleanup import execute_cleanup, plan_cleanup  # noqa: E402
from api.db import Database  # noqa: E402
from api.storage import Storage  # noqa: E402
from shared.config import get_settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="chỉ in, không xóa")
    ap.add_argument("--yes", action="store_true", help="không hỏi xác nhận")
    args = ap.parse_args()

    s = get_settings()
    if not s.db_path.exists():
        print(f"Không có DB tại {s.db_path}")
        return 1
    db = Database(s.db_path)
    plan = plan_cleanup(db)
    print(f"DB: {s.db_path}\n")
    print(plan.summary())
    if plan.empty:
        print("\nKhông có dữ liệu mock nào.")
        return 0
    if args.dry_run:
        print("\n(--dry-run: không xóa gì)")
        return 0
    if not args.yes:
        answer = input("\nXóa TOÀN BỘ các mục trên? Không hoàn tác được. Gõ 'yes' để xác nhận: ").strip().lower()
        if answer != "yes":
            print("Đã hủy.")
            return 1
    removed = execute_cleanup(db, Storage(s.storage_dir), plan, ui_cache=s.data_dir / "ui_cache")
    print(f"\nĐã xóa {len(plan.tracks)} track, {len(plan.datasets)} dataset, {len(plan.loras)} LoRA, "
          f"{len(plan.generations)} generation, {removed} file.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
