#!/usr/bin/env bash
# Chạy lại toàn bộ Link Analysis (từ thư mục link_analysis/):
#   bash run_all.sh path/to/matches.csv path/to/features.csv
set -euo pipefail
MATCHES=${1:-matches.csv}
FEATURES=${2:-features.csv}
mkdir -p results figures

echo "== Bước 1. Dựng đồ thị, tính đặc trưng gr_* (~10 giây)"
python src/build_graph_features.py --dataset "$MATCHES" --out results/graph_features_matches.csv --selftest

echo "== Bước 2. Phân tích mạng lưới + hình 1–3 (~10 giây)"
python src/network_analysis.py --matches "$MATCHES" --out results --figures figures > /dev/null

echo "== Bước 3. Đánh giá có / không có đặc trưng đồ thị + hình 4 (~25 phút)"
python src/evaluate.py --features "$FEATURES" --graph results/graph_features_matches.csv

echo "== Kiểm thử"
[ -f matches.csv ] || cp "$MATCHES" matches.csv
python tests/test_graph_features.py
