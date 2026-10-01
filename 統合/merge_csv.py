import csv
import sys
import os
import glob
import shutil


def read_header(path):
    encodings = ("utf-8-sig", "cp932")
    last_err = None
    for enc in encodings:
        try:
            with open(path, encoding=enc, newline="") as f:
                reader = csv.reader(f)
                header = next(reader)
            return header, enc
        except (UnicodeDecodeError, StopIteration) as e:
            last_err = e
            continue
    raise RuntimeError(f"{path} のヘッダーを読み込めませんでした: {last_err}")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(script_dir, "input")
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(script_dir, "output")

    if not os.path.isdir(input_dir):
        print(f"エラー: inputフォルダが見つかりません: {input_dir}")
        sys.exit(1)

    csv_files = sorted(glob.glob(os.path.join(input_dir, "*.csv")))
    if len(csv_files) < 2:
        print(f"エラー: input内の統合対象CSVが2件未満です（{len(csv_files)}件）: {input_dir}")
        sys.exit(1)

    print("対象ファイル:")
    for f in csv_files:
        print(f"  - {os.path.basename(f)}")

    headers = {}
    encodings = {}
    for f in csv_files:
        header, enc = read_header(f)
        headers[f] = header
        encodings[f] = enc

    base_file = csv_files[0]
    base_header = headers[base_file]

    mismatches = [f for f in csv_files[1:] if headers[f] != base_header]
    if mismatches:
        print()
        print("エラー: カラム（列名・順番）が一致しないファイルがあります。統合を中止します。")
        print(f"基準ファイル: {os.path.basename(base_file)}")
        print(f"  columns ({len(base_header)}): {base_header}")
        for f in mismatches:
            print(f"不一致ファイル: {os.path.basename(f)}")
            print(f"  columns ({len(headers[f])}): {headers[f]}")
        sys.exit(1)

    total_rows = 0
    row_counts = {}
    for f in csv_files:
        with open(f, encoding=encodings[f], newline="") as fh:
            reader = csv.reader(fh)
            next(reader)
            n = sum(1 for _ in reader)
            row_counts[f] = n
            total_rows += n

    basenames = [os.path.splitext(os.path.basename(f))[0] for f in csv_files]
    common_prefix = os.path.commonprefix(basenames).rstrip("_-")
    if len(common_prefix) >= 3:
        name_base = common_prefix
    else:
        name_base = os.path.basename(os.path.normpath(input_dir))
    out_name = f"{name_base}_統合{total_rows}件.csv"

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, out_name)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as out:
        writer = csv.writer(out)
        writer.writerow(base_header)
        for f in csv_files:
            with open(f, encoding=encodings[f], newline="") as fh:
                reader = csv.reader(fh)
                next(reader)
                for row in reader:
                    writer.writerow(row)

    print()
    print("統合が完了しました。")
    for f in csv_files:
        print(f"  {os.path.basename(f)}: {row_counts[f]}件")
    print(f"合計: {total_rows}件")
    print(f"出力: {out_path}")

    done_dir = os.path.join(os.path.dirname(os.path.normpath(input_dir)), "input_done")
    os.makedirs(done_dir, exist_ok=True)
    for f in csv_files:
        shutil.move(f, os.path.join(done_dir, os.path.basename(f)))
    print(f"統合済みファイルを移動しました: {done_dir}")


if __name__ == "__main__":
    main()
