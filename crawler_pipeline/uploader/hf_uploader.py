"""
crawler_pipeline/uploader/hf_uploader.py
Uploads target season datasets and master entities directly to Hugging Face Hub Dataset repo.
Strictly isolates target season and avoids touching old seasons.
"""

import os
import datetime
from huggingface_hub import HfApi
from crawler_pipeline.config import DATA_DIR, MASTER_DIR, DEFAULT_HF_REPO_ID


def sync_to_huggingface(repo_id: str = DEFAULT_HF_REPO_ID, token: str = None, season: str = "2026_2027", all_seasons: bool = False) -> None:
    """Sync target season (or all seasons) and master entities directly to Hugging Face Datasets repository."""
    print("=" * 80)
    print(f"SYNCING TO HUGGING FACE DATASET: {repo_id}")
    print("=" * 80)

    if not token:
        token = os.environ.get("HF_TOKEN")

    try:
        api = HfApi(token=token)

        # Check if repo exists, create if not
        try:
            api.repo_info(repo_id=repo_id, repo_type="dataset")
            print(f"[+] Repository '{repo_id}' found on Hugging Face.")
        except Exception:
            print(f"[*] Creating repository '{repo_id}' on Hugging Face...")
            api.create_repo(repo_id=repo_id, repo_type="dataset", private=False)

        now_str = datetime.datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        if all_seasons or season == "all":
            print(f"[*] Uploading entire dataset (all seasons + master entities) to Hugging Face...")
            api.upload_folder(
                folder_path=str(DATA_DIR),
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"Sync complete EPL dataset (2000-2027) - {now_str}",
            )
        else:
            commit_msg = f"Automated Daily EPL Update {season} - {now_str}"
            s_folder = os.path.join(DATA_DIR, season)
            print(f"[*] Uploading target season '{season}' to Hugging Face...")
            api.upload_folder(
                folder_path=str(s_folder),
                path_in_repo=season,
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=commit_msg,
            )

            # Upload master entities (clubs, players mapping) to keep them in sync
            print(f"[*] Uploading 00_master_entities to Hugging Face...")
            api.upload_folder(
                folder_path=str(MASTER_DIR),
                path_in_repo="00_master_entities",
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"Sync master entities - {now_str}",
            )

        # 3. Upload README.md dataset card if present in data/
        readme_path = os.path.join(DATA_DIR, "README.md")
        if os.path.exists(readme_path):
            api.upload_file(
                path_or_fileobj=readme_path,
                path_in_repo="README.md",
                repo_id=repo_id,
                repo_type="dataset",
                commit_message="Update dataset card README.md",
            )

        print(f"\nSUCCESS: Data synced to https://huggingface.co/datasets/{repo_id}")
    except Exception as e:
        print(f"[!] Hugging Face sync error: {e}")

