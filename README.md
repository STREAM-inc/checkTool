# checkTool

## インストール

必要なもの：Windows 10 / 11、Python 3.8 以上（`py` または `python` コマンドが使えること）

1. 取得する

   ```bash
   git clone https://github.com/STREAM-inc/checkTool.git
   ```

2. `checkTool` の直下の `APIキー.example.txt` を **`APIキー.txt`** という名前でコピーし、中身を自分の Backlog の API キー 1 行だけに書き換える（目検ツールで使用）
   - API キーは Backlog の「個人設定 → API」で発行できます
   - ファイルの中身はそのままキーとして使われるので、説明文やコメントは書かないでください

## フォルダ構成

```
checkTool/
├─ README.md                  ← このファイル
├─ .gitignore                 ← Git に入れないもの（APIキー・setting.json・顧客データ）
├─ APIキー.example.txt        ← APIキー.txt のひな形
├─ APIキー.txt                ← 自分で作る（Git には入れない）
├─ setting.example.json       ← setting.json のひな形
├─ setting.json               ← 全ツールの設定（自分で作る・Git には入れない）
│
├─ 目検ツール.bat              ← 目検ツールの起動（ダブルクリック）
│
├─ 目検ツール/                 ← Backlog 課題を見ながら納品CSVを自動修正・チェック（ブラウザ画面）
│   ├─ README.md / README.txt（詳しい使い方）
│   ├─ 子課題テンプレート.txt   ← 子課題の「編集」で使うテンプレート
│   ├─ app.py / mokken_core.py / areacode.py / backlog.py / build_shigai.py
│   ├─ static/index.html       ← 画面
│   ├─ data/                   ← 総務省「市外局番の一覧」
│   ├─ download/               ← 「この添付で目検」でBacklogから落とした添付（日付 ＞ 課題番号ごと。自動で作られる・Git には入れない）
│   ├─ output/                 ← 「納品用CSVを出力」したものだけ：日付/result に納品用CSV、日付/log に修正ログ・除外行（Git には入れない）
│   └─ work/                   ← 処理するたびに作る作業用の納品用CSV・修正ログ・除外行（自動で作られる・Git には入れない）
│
├─ 郵便番号補完ツール/          ← 住所から空欄の郵便番号を補完（目検ツールもこのデータを使う）
│   ├─ README.md
│   ├─ 郵便番号補完.bat
│   ├─ fill_zip.py
│   └─ data/utf_ken_all.csv    ← 日本郵便の郵便番号データ
│
├─ 採用担当者名寄せ/            ← ハローワークの求人データから採用担当者名を埋める
│   ├─ README.md / README.txt（詳しい使い方）
│   ├─ 採用担当者名寄せ.bat
│   └─ fill_recruiter.py / hw_index.py
│
├─ 自動処理_チェーン店除外/      ← チェーン店・「〇〇店」の行を除外
│   ├─ README.md
│   ├─ 実行.bat
│   ├─ 追加チェーン除外リスト.txt ← 自分で追加するチェーン名
│   └─ process_folder.py
│
└─ 統合/                       ← 同じ列の CSV を 1 つにまとめる
    ├─ README.md
    ├─ csv統合.bat
    └─ merge_csv.py
```

各ツールの `input` / `output` などの作業フォルダは、起動すると自動で作られます（顧客データが入るので Git には入れません）。

## 設定

設定は `checkTool` 直下の **`setting.json`** 1 つにまとめています。`setting.example.json` を `setting.json` という名前でコピーして書き換えてください（無くても初期値で動きます）。

- 相対パスは `checkTool` フォルダからの場所（例: `目検ツール/checks`）
- Windows のパスは `\` を 2 つ重ねる（`"C:\\Users\\..."`）か `/` で書く（`"C:/Users/..."`）
- `_` で始まる項目は説明なので、プログラムは読みません
- 書き間違いがあると、起動したときに「setting.json の書き方が間違っています（〇行目）」と出て止まります

```json
{
  "目検ツール": {
    "BACKLOG_SPACE": "https://streeeeeam.backlog.com",
    "APIKEY_FILE": "APIキー.txt",
    "CHECKS_DIR": "目検ツール/checks",
    "TASKS_FILE": "目検ツール/tasks.json",
    "CUSTOMER_RULES": "目検ツール/顧客ルール.json",
    "PORT": 8765,
    "HW_CSV": ""
  },
  "採用担当者名寄せ": {
    "HW_CSV": ["C:/path/to/ハローワーク.csv"]
  }
}
```

**目検ツール**

| 設定 | 初期値 | 内容 |
| --- | --- | --- |
| `BACKLOG_SPACE` | `https://streeeeeam.backlog.com` | Backlog スペースの URL |
| `APIKEY_FILE` | `APIキー.txt` | API キーを書いたファイル |
| `CHECKS_DIR` | `目検ツール/checks` | チェック状態の保存先。共有フォルダにするとオーナーと同じ状態を見られる |
| `TASKS_FILE` | `目検ツール/tasks.json` | 左端のタスクのタブの保存先（自分用） |
| `CUSTOMER_RULES` | `目検ツール/顧客ルール.json` | 顧客ルールのファイル。共有フォルダに置くとみんなで同じルールを見られる |
| `PORT` | `8765` | ブラウザで開くポート |
| `HW_CSV` | （空欄） | 市外局番チェックの参考にするハローワーク CSV。空欄なら下の「採用担当者名寄せ」の最初の CSV を使う |

**採用担当者名寄せ**：`HW_CSV` にハローワーク CSV の場所（ファイルかフォルダ）を並べる。複数書くと全部取り込みます

`setting.json` が無いときは、前の形式の各フォルダの `設定.txt` を読みます。

**チェーン店除外**：チェーンだと確認できた店名を `自動処理_チェーン店除外/追加チェーン除外リスト.txt` に 1 行ずつ追記

## 起動

`.bat` をダブルクリックします。目検ツールは `checkTool` 直下、ほかのツールは各フォルダの中にあります。

| ツール | 起動するファイル |
| --- | --- |
| 目検ツール | `目検ツール.bat`（ブラウザで `http://localhost:8765` が開く。黒いウィンドウを閉じると終了） |
| 郵便番号補完ツール | `郵便番号補完ツール/郵便番号補完.bat` |
| 採用担当者名寄せ | `採用担当者名寄せ/採用担当者名寄せ.bat` |
| チェーン店除外 | `自動処理_チェーン店除外/実行.bat` |
| CSV 統合 | `統合/csv統合.bat` |
