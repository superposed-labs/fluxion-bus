# Fluxion Bus

[English](README.md) | [简体中文](README.zh-CN.md) | [日本語](README.ja.md)

https://github.com/user-attachments/assets/7ff8be14-f4e6-4bd9-9ceb-bbf425fafba3

_※デモ動画はモックデータを用いた動作イメージであり、実際のリアルタイム操作画面ではありません。_

Fluxion Bus は、Fluxion macOS メニューバーアプリおよびローカル Agent ゲートウェイのコアとなるオープンソースプロジェクトです。

**Fluxion** を導入すると、普段お使いのプライマリ AI エージェント（主 Agent）から、単一のローカル MCP サーバーを経由して、特定範囲のサブタスク（scoped subtasks）を **Codex**、**Claude Code**、および **Antigravity** にシームレスに委任できるようになります。

エージェントの対話画面を離れることなく、Fluxion が自動的にタスクを別のプロバイダー（AI サービス提供元）にルーティングし、セッション状態を維持しながら、進捗と実行結果を報告します。また、安全なロールバック（Revert）に備えて、すべてのファイル変更履歴を自動で記録します。

さらに、Fluxion はプロバイダー側のクォータ（利用枠）ウィンドウをスマートに監視します。リセットを検知すると通知を送信するだけでなく、リセット直後に極小のエージェント呼び出し（Auto-ping）を自動実行することで、即座に次のローリングクォータウィンドウを開始できます。

クォータおよび使用量の監視は、Fluxion を経由したタスクに限定されません。各プロバイダーの API、ローカルエージェントサービス、またはローカル履歴から直接用量をインポートして集計するため、Fluxion 自体のタスク記録のみから間接的にクォータを推定するような不正確さはありません。

ローカルファースト、シングルテナント、完全なセルフホスト設計。Fluxion アカウントの作成や SaaS 依存はなく、デフォルトでの公開範囲は `127.0.0.1`（ローカルホスト）のみに制限されています。

## なぜ Fluxion なのか

### プライマリエージェント画面から離れない、クロスプロバイダーのタスク委任

```text
┌────────────────────────────────────────────┐
│ プライマリエージェント                      │
│ Codex / Claude Code / Antigravity          │
└─────────────────────┬──────────────────────┘
                      │ MCP: 特定範囲のサブタスクを委任
                      ▼
              ┌──────────────────┐
              │ Fluxion MCP      │
              │ ルート + 監視    │
              └────────┬─────────┘
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
         Codex     Claude Code   Antigravity
           │            │            │
           └────────────┼────────────┘
                        ▼
       ステータス / 実行結果 / 変更ファイル / ロールバック (Revert)
                        │
                        ▼
                プライマリエージェント
```

- **インテリジェントなルーティング**：各サブタスクを最適なプロバイダーに自動でルーティング。
- **セッションの永続化**：複数回にわたる呼び出しを跨いで、タスクエージェント（Executor）ネイティブなセッションを継続。
- **安心のパーミッション管理**：読み取り専用の調査モード、または明示的な承認を必要とする書き込み（編集）モードを選択可能。
- **透明性の高いタスク管理**：非同期タスクの実行ステータス、リアルタイムログ、生成物（Artifacts）、および変更されたファイルを簡単に検査。
- **安全なファイル復元**：変更されたテキストファイルをレビュー後、ワンクリックで元の状態へロールバック（Revert）可能。

### クォータのリセットを有効な利用可能ウィンドウに変換

```text
  Claude クォータ     Codex クォータ     Antigravity クォータ
        └───────────────┬──────────────────┘
                        ▼
               ┌─────────────────┐
               │ Fluxion クォータ │
               │ 監視 + スケジュール│
               └────────┬────────┘
                        │
           ┌────────────┼──────────────┐
           ▼            ▼              ▼
         Web UI      macOS アプリ      リセット検出
                                        │
                                  自動 Ping + 通知
                             LINE/Slack/Telegram/WeChat/Feishu/QQ
```

- **統合クォータダッシュボード**：異なるプロバイダーの残りクォータとリセットまでのカウントダウンを直感的に確認。
- **包括的な用量監視**：Fluxion の外部で行われた使用量も含め、プロバイダー/アカウントのクォータをリアルタイム監視。
- **クロスプラットフォーム**：macOS または Linux 上で動作するブラウザベースの Web コンソールを提供。
- **快適なネイティブ macOS 体験**：メニューバーでのクォータ確認、サービスの起動・停止、初期設定が可能なネイティブ macOS アプリを用意。
- **リセット自動検知**：プロバイダー側のクォータリセットイベントを正確に検知。
- **アクティブウィンドウ開始**：リセット検知後、極小のエージェント呼び出しを自動で実行し、次のローリングウィンドウを即座に有効化。
- **柔軟なマルチチャネル通知**：LINE、Slack、Telegram、WeChat、Feishu、QQ を介してリセット通知を柔軟に送信。

### ローカルエージェントをリモートで制御

外出先などパソコンから離れている時でも、LINE、Slack、Telegram、WeChat、Feishu、QQ を使ってタスクを送信できます。Fluxion はメッセージをローカルの Codex、Claude Code、または Antigravity タスクエージェントにルーティングし、進捗状況と最終結果を同じチャットスレッド内にリアルタイムで返信します。

```text
スマートフォン / リモートデバイス
LINE/Slack/Telegram/WeChat/Feishu/QQ
          │
          ▼
  Fluxion メッセージ Gateway
          │
          ▼
Codex / Claude / Antigravity
          │
          ▼
    進捗状況の更新 + 最終結果
```

リモートでのやり取りでもタスクエージェントのセッション状態が維持されるため、追加入力で同じタスクのコンテキストを継続して会話できます。また、チャット内の制御コマンドを使って、最近のタスク一覧の確認、ゲートウェイのステータスチェック、セッションのクリア、キュー内または実行中タスクの強制キャンセルを行うことも可能です。

WeChat では安全な iLink QR コードログインを使用します。アカウントの連携とチャネルの有効化を一度行うだけで、他のチャネルと同様に統一されたメッセージ Gateway として利用可能です。

## プラットフォームサポート

| 機能特性 | macOS | Linux | Windows |
| --- | --- | --- | --- |
| MCP 跨プロバイダー委任 | サポート | 動作見込み（未検証） | 未検証 |
| Web クォータコンソール | サポート | 動作見込み（未検証） | 未検証 |
| 自動 Ping とリセット通知 | サポート | 動作見込み（未検証） | 未検証 |
| ネイティブ macOS アプリ | macOS 12+ | 利用不可 | 利用不可 |

現在の実装仕様に基づき、非ネイティブの機能は Linux 上でも論理的に動作する見込みですが、現時点では手動での動作検証は行われていません。

メニューバーアプリは macOS 12 以降に対応しています。なお、「ログイン時に自動起動」オプションは macOS 13 以降の API を使用するため、macOS 12 上では自動的に無効化されます。

プロバイダーのクォータ取得は、互換性のあるローカルの資格情報またはサービスに依存します。例えば、Antigravity のライブクォータを取得するには、ローカルでその Sidecar プロセスが稼働している必要があります。ダッシュボードに表示される使用量は、Fluxion 経由のタスク数からカウントするのではなく、各サービス提供元から直接インポートした実数値です。

## インストールと検証

前提条件：

- **タスクエージェント**：システムに少なくとも 1 つの認証済みタスクエージェント CLI 工具（`codex`、`claude`、または `agy`）がインストールされていること。
  - Codex：スタンドアロン CLI、または Codex デスクトップアプリのいずれか。デスクトップアプリにバンドルされた CLI は macOS 上で自動検出され、ログイン済みの状態で認証されます。
- **Python 環境**：CLI/バックエンドのセットアップに Python 3.12+ （Python 3.13 推奨）が必要です。macOS アプリは、必要に応じて Homebrew 経由で `python@3.13` を自動インストールできます。
- **Node.js**：ローカル環境で Web コンソールのフロントエンドを再構築（リビルド）する場合のみ、Node 18+ が必要です。

### macOS デスクトップアプリ（推奨）

Apple Silicon（Mシリーズチップ）の macOS ユーザーは、[最新の GitHub リリース](https://github.com/superposed-labs/fluxion-bus/releases/latest)から事前ビルドされた `Fluxion.app` の DMG ファイルをダウンロードし、`/Applications` ディレクトリにドラッグ＆ドロップして開いてください。*(注：事前ビルドされたリリース DMG は Apple Silicon 搭載 Mac 専用です。Intel プロセッサ搭載 Mac ユーザーは、ソースからビルドするか CLI 経由でインストールしてください)。*

[Homebrew](https://brew.sh) を使ってインストールすることもできます：

```bash
brew install --cask superposed-labs/tap/fluxion
```

この cask は同じリリース DMG をダウンロードし、隔離属性を自動的に解除するため、Gatekeeper の操作は不要です。ただしアプリ自体は未署名のままです —— 詳細は以下を参照してください。

現在配布されている DMG イメージは開発者署名および公証が行われていません。そのため、初回起動時に macOS の Gatekeeper 機能によって開発元が未検証であるとしてブロックされる場合があります。公式の [GitHub Releases](https://github.com/superposed-labs/fluxion-bus/releases) ページからダウンロードし、`SHA256SUMS` を検証した上で実行する場合は、一度アプリの起動を試みた（警告が表示された）後、**システム設定 -> プライバシーとセキュリティ** に進み、画面下部にある Fluxion の警告メッセージ横の **このまま開く** をクリックしてから、アプリを再起動してください。あるいは、コマンドライン操作に慣れている場合は、以下のコマンドを実行して隔離（quarantine）属性を直接解除することも可能です。

```bash
xattr -dr com.apple.quarantine /Applications/Fluxion.app
```

初回起動時に、Fluxion は `~/.local/share/fluxion` を管理対象のバックエンドパスとして使用し、**Install / Repair（インストール / 修復）** を提案します。アプリは、アプリ内にバンドルされたソーススナップショットと依存関係の Wheel ファイルからバックエンドを自動インストールし、仮想環境 `.venv` を作成し、`.env` を初期化して、ローカルサービスを開始します。このセットアップ中、git や外部ネットワークへのアクセス、Xcode Command Line Tools のインストール、ローカルでの Node プロジェクトビルドは不要です。

Python 3.12+ がまだ利用できず、Homebrew がインストールされている場合、インストーラーは Homebrew を使用して `python@3.13` を自動でインストールします。Homebrew がインストールされていない場合、アプリはセットアップを開始する前に python.org の公式インストーラーに案内します。`codex`、`claude`、`agy` などのタスクエージェント CLI は、別途あらかじめインストールして認証を通しておく必要があります。

### AI エージェントによる自動設定（CLI / MCP）

CLI を優先して使用したい場合、MCP サーバーの登録を行いたい場合、または非デスクトップ環境へのインストールを行いたい場合、現在使用している AI エージェント自身に、依存関係のチェックからインストールスクリプトの実行、クライアントへの MCP サーバーの登録、動作検証までをすべて自動で完了させることができます。

Fluxion を動作させたいプロジェクトディレクトリから、以下のテキストを Claude Code、Codex、または Antigravity にそのまま貼り付けて送信してください。

```text
Read https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/docs/agent-install.md
and follow it to install and configure Fluxion on this machine. Use the current
directory as the first authorized workspace, register the MCP server with the
client you are running in, then run the verification steps and report the results.
```

エージェントは [docs/agent-install.md](docs/agent-install.md) の記述に従い、後述する手動インストーラーと同等の処理を順に実行します。処理が完了すると、バックエンド CLI、MCP 登録状況、および Web コンソールの静的アセットビルド結果を含むコンポーネントごとのセットアップ完了レポートを出力します。なお、macOS デスクトップアプリはリリース用の DMG ファイル経由で個別に提供されます。

### 手動インストール

現在のユーザー向けに Fluxion のバックエンドをインストールまたはアップデートします。

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | bash -s -- --no-desktop
```

このインストーラーはバックエンドを `~/.local/share/fluxion` に配置し、関連コマンドへのシンボリックリンクを `~/.local/bin` 内に作成することで、CLI、メッセージ Gateway、および MCP コマンドを使えるようにします。本スクリプトによるインストールは、CLI 単体での運用や開発用のワークフローに適しています。アップデート時は、現在の `.env` や `data/` の内容を保持したまま、同じコマンドを実行するだけで最新版に更新できます。

デフォルトでは、インストールコマンドを実行したディレクトリが書き込み許可のある最初のワークスペースとして自動登録されます。別のパスを指定したい場合は、以下のように環境変数で上書きしてください。

```bash
curl -fsSL https://raw.githubusercontent.com/superposed-labs/fluxion-bus/main/scripts/install.sh \
  | FLUXION_WORKSPACE=/absolute/path/to/project bash -s -- --no-desktop
```

設定とデータをタイムスタンプ付きのバックアップとして安全に退避しつつ、Fluxion をアンインストールする場合：

```bash
~/.local/share/fluxion/scripts/uninstall.sh
```

すべての設定ファイルおよびタスク履歴データも含めて完全に削除したい場合のみ、`--purge` オプションを付与してください。

### 開発用インストール

Fluxion 本体のコード開発のためにソースコードをチェックアウトして配置する場合：

```bash
git clone git@github.com:superposed-labs/fluxion-bus.git
cd fluxion-bus

python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# インストール済みのタスクエージェントを自動検出し、絶対パスが含まれる最小限の .env ファイルを作成します
fluxion init

# 現在の設定、各タスクエージェントの動作状況、およびワークスペースの権限を確認します
fluxion doctor

# 読み取り専用タスクを実行して、ローカルの実行環境を検証します
fluxion run "Summarize this project and explain how to run its tests."
```

書き込み（ファイル編集）を明示的に許可して実行する場合：

```bash
fluxion run --write "Fix the failing tests."
```

別のディレクトリに対して Fluxion のワークスペース初期化を行う場合：

```bash
fluxion init --workspace /absolute/path/to/project
fluxion doctor --workspace /absolute/path/to/project
fluxion run --workspace /absolute/path/to/project "Inspect this project."
```

`fluxion init` コマンドは意図的に最小限の `.env` のみを作成します。手動設定用のひな形として [`.env.example`](.env.example) も同梱しています。より高度な手動カスタマイズについては、[`.env.advanced.example`](.env.advanced.example)、[設定ガイド](docs/configuration.md)、および [`scripts/install.sh`](scripts/install.sh) をご参照ください。

## MCP 委任クイックスタート

すでに作業を行っているプライマリエージェントに `fluxion-mcp` を登録します。Claude Code、Codex、および Antigravity 用の完全なクライアント固有の例は、[MCP 参考ドキュメント](docs/mcp.md#client-setup) にあります。

Claude Code 登録例：

```bash
claude mcp add -s user \
  -e FLUXION_ENV_FILE=<fluxion-repo>/.env \
  -e FLUXION_WORKSPACE_ROOT=<fluxion-repo> \
  -e FLUXION_DATA_DIR=<fluxion-repo>/data \
  fluxion -- <fluxion-repo>/.venv/bin/fluxion-mcp
```

登録完了後、プライマリエージェントは特定のフォーカスされたサブタスクを委任できます。

```json
{
  "agent": "claude",
  "project": "web",
  "profile": "inspect",
  "mode": "read-only",
  "prompt": "Investigate why the login form is submitting twice."
}
```

Fluxion は `run_id` を返します。プライマリエージェントは、同じ MCP サーバーを介して、ステータスの検査、結果の取得、実行のキャンセル、変更されたファイルのレビュー、またはレビュー済みの書き込み実行の安全なリバート（Revert）を行うことができます。

マルチプロジェクトで使用する場合は、`FLUXION_PROJECTS_FILE` でプロジェクトキーをマップします。[プロジェクト登録ガイド](docs/configuration.md#project-registry) を参照してください。

## クォータ監視クイックスタート

### Web コンソール

事前ビルド版アプリをインストールしたか、インストールスクリプトを使用した場合は、コンソールは既に起動可能です。直接起動してください。

```bash
fluxion-web                  # 起動後、ブラウザで http://127.0.0.1:8765 にアクセス
```

*（※Git クローンから実行している場合や、静的アセットを再構築する必要がある場合は、まず `cd web && npm install && npm run build && cd ..` を実行してから、このコマンドを起動してください）*。

### macOS メニューバーアプリ

事前ビルド済みの `Fluxion.dmg` をダウンロードした場合は、`/Applications` にドラッグして開くだけです。

ローカルのソースコードから macOS アプリをビルドする場合（これは、Apple Silicon または Intel にかかわらず、マシンのアーキテクチャに合わせてネイティブにコンパイルされます）：

```bash
./desktop/build.sh
open desktop/Fluxion.app
```

原生メニューバーアプリは、クォータ監視、自動 Ping、リセット通知、および関連する常駐プロセスの起動・管理用のUIを提供します。実際のバックグラウンドでの自動 Ping および通知処理は、`fluxion-scheduler` によって実行されます。これは、Linux 上でも macOS アプリなしで動作します。

コンパイルされたアプリはリポジトリ内に残すことも、`/Applications` にコピーすることもできます。リポジトリの外で起動された場合、Fluxion ソースのチェックアウトパスを選択するよう求められ、そのパスが `~/Library/Application Support/Fluxion/` に保存されます。

ターミナルでスケジュール監視守护プロセスを起動する場合：

```bash
fluxion-scheduler
```

プロバイダーデータ源、設定、および常時実行のデプロイについては、[クォータ監視ガイド](docs/quota-monitoring.md) および [スケジューラーガイド](docs/scheduler.md) を参照してください。

## メッセージングチャネル

`fluxion-gateway` は、LINE、Slack、Telegram、WeChat、Feishu、および QQ からのリモートタスクを受け付け、MCP やローカル CLI で使用されるものと同じルーターを介して送信します。同じ会話内で実行ステータスと最終結果を返信します。

メッセージ Gateway を起動する：

```bash
fluxion-gateway
```

チャネルとワークスペースの細かな設定については、[設定ガイド](docs/configuration.md#messaging-channel-configuration) を参照してください。

## ドキュメント

- [システムアーキテクチャ](docs/architecture.md) — 完全なシステム構成図、各コンポーネントのインターフェース、共有状態、およびディレクトリレイアウト
- [エージェント支援インストール](docs/agent-install.md) — AI エージェントが実行するためのステップバイステップのインストール手順
- [MCP リファレンス](docs/mcp.md) — 各種クライアントセットアップ、利用可能ツール、ステータス定義、キャンセル、および安全なリバートフロー
- [クォータ監視ガイド](docs/quota-monitoring.md) — プロバイダーデータ源、Web コンソール、macOS アプリ、プライバシー保護、および通知設定
- [macOS アプリガイド](desktop/README.md) — 配布用アプリパッケージのビルド、管理バックエンド設定、システムインストール、および開発用オーバーライド
- [使用率統計](docs/usage-statistics.md) — エージェント履歴の解析対象、Fluxion 委任から独立した用量計測、費用見積もり、および Fast モードの制限
- [スケジューラーガイド](docs/scheduler.md) — 自動 Ping、クォータリセット検知トリガー、cron ルール設定、およびサービスデプロイ
- [設定ガイド](docs/configuration.md) — タスクエージェントのカスタマイズ、権限管理、各種メッセージチャネル、Web UI、および環境変数
- [サービスデプロイ](deploy/README.md) — launchd および systemd のサービステンプレートの適用方法

## 开源协议

[Apache License 2.0](LICENSE) — [NOTICE](NOTICE) も参照してください。
