"""
sync_from_hf.py
Synchronizes and downloads the complete Premier League dataset from Hugging Face Hub to local data/ directory.
Usage:
    python sync_from_hf.py
    python sync_from_hf.py --repo-id Toibinguz/epl-premier-league-dataset
"""

import os
import sys
import argparse
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"

DEFAULT_REPO_ID = os.environ.get("HF_REPO_ID", "Toibinguz/epl-premier-league-dataset")


def sync_from_huggingface(repo_id: str, local_dir: Path, token: str = None) -> None:
    print("=" * 80)
    print(f"SYNCING DATASET FROM HUGGING FACE: {repo_id}")
    print(f"LOCAL DESTINATION: {local_dir}")
    print("=" * 80)

    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("[!] huggingface_hub is not installed. Installing required dependencies...")
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "huggingface_hub"])
        from huggingface_hub import snapshot_download

    token = token or os.environ.get("HF_TOKEN")
    token_candidates = [
        ROOT_DIR / "crawler_pipeline" / "hf_access_key.txt",
        ROOT_DIR / "access_token_hf.txt"
    ]
    if not token:
        for tc in token_candidates:
            if tc.exists():
                try:
                    with open(tc, "r", encoding="utf-8") as f:
                        token = f.read().strip()
                        if token:
                            break
                except Exception:
                    pass

    try:
        local_dir.mkdir(parents=True, exist_ok=True)
        print(f"[*] Downloading dataset files from https://huggingface.co/datasets/{repo_id}...")
        downloaded_path = snapshot_download(
            repo_id=repo_id,
            repo_type="dataset",
            local_dir=str(local_dir),
            token=token,
            max_workers=8,
        )
        print("\n" + "=" * 80)
        print(f"SUCCESS: ALL DATASETS SYNCED TO LOCAL: {downloaded_path}")
        print("=" * 80)
    except Exception as e:
        print(f"\n[!] Error syncing from Hugging Face: {e}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Sync Premier League dataset from Hugging Face to local")
    parser.add_argument("--repo-id", default=DEFAULT_REPO_ID, help="Hugging Face Dataset repo ID")
    parser.add_argument("--local-dir", default=str(DATA_DIR), help="Local target directory")
    parser.add_argument("--token", default=None, help="Hugging Face API token (optional for public dataset)")
    args = parser.parse_args()

    sync_from_huggingface(
        repo_id=args.repo_id,
        local_dir=Path(args.local_dir),
        token=args.token
    )


if __name__ == "__main__":
    main()
