# Setup and run benchmarks

Tài liệu này hướng dẫn thiết lập repository từ một checkout mới, cấu hình model,
build môi trường Docker, chạy đúng một sample, và chạy toàn bộ một benchmark.
Phần 1–11 dùng Bash trên Ubuntu; phần 12 là bản chạy trên Windows Git Bash.
Phần 13 dùng chung cho hai hệ điều hành sau khi thiết lập đúng shell.

## 1. Yêu cầu hệ thống

- Git.
- `uv`.
- Ubuntu: Docker Engine và Docker Compose v2; Windows: Docker Desktop ở chế độ Linux containers.
- Quyền truy cập model/API sẽ sử dụng.
- Quyền truy cập hai Git submodule của repository.

Cài `uv` trên Ubuntu nếu chưa có:

```bash
sudo apt-get update
sudo apt-get install -y curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
source "$HOME/.local/bin/env"
uv --version
```

Chạy installer bằng user sẽ chạy benchmark, không thêm `sudo` trước installer.
Lệnh `source` cập nhật PATH ngay trong shell hiện tại. Không cần cài Python
trước để cài `uv`; bước `uv sync` bên dưới sẽ chuẩn bị Python cho project.
Windows Git Bash dùng lệnh cài riêng ở phần 12.
Nguồn: [hướng dẫn cài đặt chính thức của uv](https://docs.astral.sh/uv/getting-started/installation/).

Kiểm tra nhanh:

```bash
git --version
uv --version
docker info
```

`docker info` phải hiển thị cả thông tin client và server. Nếu lệnh này báo
`permission denied` hoặc không kết nối được daemon, cần khởi động/sửa quyền
Docker daemon (Ubuntu) hoặc Docker Desktop (Windows) trước khi tiếp tục.

## 2. Khởi tạo submodule

Từ thư mục gốc của repository:

```bash
git submodule sync --recursive
git submodule update --init --recursive
git submodule status
```

Kết quả thành công không có dấu `-` trước commit của `LOCA-bench` và
`software-agent-sdk`.

`software-agent-sdk` mặc định dùng SSH. Nếu máy chưa cấu hình GitHub SSH key,
override URL của submodule sang HTTPS rồi chạy lại:

```bash
git submodule sync --recursive
git config submodule.software-agent-sdk.url \
  https://github.com/malusamayo/software-agent-sdk.git
git submodule update --init --recursive
```

Nếu HTTPS báo `Repository not found`, tài khoản GitHub hiện tại chưa có quyền
truy cập repository đó.

Nếu `LOCA-bench` đã có commit nhưng thư mục chỉ chứa `.git`, khôi phục index và
working tree từ commit của submodule:

```bash
git -C LOCA-bench restore --source=HEAD --staged --worktree -- .
```

Xác minh các phần cần cho Stock Alert:

```bash
test -d LOCA-bench/gem/envs/woocommerce_stock_alert_s2l && echo OK
test -d LOCA-bench/mcp_convert && echo OK
test -d software-agent-sdk/openhands-sdk && echo OK
```

Cả ba lệnh phải in `OK`.

## 3. Cài dependency Python

Project yêu cầu Python 3.14. `uv` có thể tự quản lý phiên bản Python phù hợp.
Đặt cache trong workspace để dễ quản lý:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
uv sync
```

Lệnh này cũng tạo `uv.lock`. Dockerfile của các task cần file lock này khi
build image. Ở các lần setup sau, có thể dùng:

```bash
uv sync --frozen
```

## 4. Cấu hình model

Runner đọc model registry từ `configs/models.yaml` và biến môi trường từ `.env`
ở thư mục gốc. Hai file này bị ignore để tránh commit credential.

Tạo thư mục config nếu chưa có:

```bash
mkdir -p configs
```

Ví dụ cho một model được phục vụ qua API tương thích OpenAI:

```yaml
# configs/models.yaml
models:
  - name: qwen3.6-35b-a3b-fp8
    model: openai/<served-model-id>
    api_base: ${QWEN_API_BASE}
    api_key: ${QWEN_API_KEY}
    reasoning_effort: none
    extra_body:
      chat_template_kwargs:
        enable_thinking: false
```

`name` là alias được sử dụng trong task config và manifest. `model` là ID thực
tế mà model server/provider nhận biết. Một số gateway nội bộ chấp nhận model ID
không có prefix; với LiteLLM/OpenAI-compatible thông thường nên dùng prefix
`openai/`.

Hai trường `reasoning_effort: none` và `enable_thinking: false` tắt reasoning
của Qwen ở cả lớp SDK và Qwen chat template. `src.utils.build_sdk_llm` chuyển
`extra_body` trong model registry thành request body của OpenAI-compatible API.

Ví dụ `.env` với model server mà cả host và container truy cập được:

```dotenv
QWEN_API_BASE=http://<HOST_IP>:8000/v1
QWEN_API_KEY=replace-with-the-real-key
```

Trên Ubuntu, thay `<HOST_IP>` bằng IP của host truy cập được từ container;
model server phải lắng nghe trên interface đó (ví dụ `0.0.0.0`), không chỉ
`127.0.0.1`. Runner hiện không tự thêm mapping `host.docker.internal` cho
Docker Engine Linux. Trên Docker Desktop Windows có thể dùng
`http://host.docker.internal:8000/v1`. Với API public, dùng URL HTTPS của provider.

Mọi model được tham chiếu bởi các trường sau đều phải có alias tương ứng trong
`configs/models.yaml`:

- `model_name`: model chạy agent.
- `eval_lm`: model chấm điểm, nếu evaluator của task cần LLM.
- `reflection_lm`: model reflection/proposal khi chạy GEPA.

Stock Alert dùng evaluator rule-based, nên không cần `eval_lm`. Có thể comment
trường này trong `tasks/woocommerce_stock_alert_s2l/run.yaml` thay vì cấu hình
thêm Gemini.

Kiểm tra registry có thể được load:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
uv run python -c "from src.utils import LM_DICT; print(sorted(LM_DICT))"
```

Lệnh trên chỉ kiểm tra cấu hình, chưa gửi request inference.

## 5. Credential Vertex AI

Nếu dùng Vertex AI, đặt service-account JSON tại `.vertex-ai.json` hoặc cấu
hình đường dẫn qua biến môi trường phù hợp với model registry.

Luồng agentic Docker hiện mount `.vertex-ai.json`. Nếu hoàn toàn không dùng
Vertex AI, có thể tạo một JSON placeholder hợp lệ nếu file chưa tồn tại (không ghi đè credential thật):

```bash
test -e .vertex-ai.json || printf '%s\n' '{}' > .vertex-ai.json
```

Không commit credential thật vào Git.

## 6. Build Docker image của task

Compose yêu cầu biến môi trường `UID`. Bash có biến `UID` chỉ đọc, nên dùng
`env UID=...` cho từng lệnh Compose. Dùng UID của user Ubuntu hiện tại; nếu
đang chạy shell root, chọn UID 1000 vì Dockerfile tạo user mới bằng `useradd`:

```bash
container_uid="$(id -u)"
if [ "$container_uid" -eq 0 ]; then container_uid=1000; fi
env UID="$container_uid" docker compose config --services
```

Build riêng image của Stock Alert:

```bash
env UID="$container_uid" docker compose build woocommerce_stock_alert_s2l
docker image inspect woocommerce_stock_alert_s2l:latest
```

Với task khác, tìm `server_image` trong `tasks/<task_id>/run.yaml`, rồi build
service tương ứng nếu service đó có trong `docker compose config --services`:

```bash
task_id="machine_operating_s2l"
env UID="$container_uid" docker compose build "$task_id"
```

Một số benchmark có môi trường ngoài repository, ví dụ WebArena hoặc
RefactorBench. Đọc metadata/config của task và phần "Run One Experiment From Raw
Data" trong README trước khi chạy những task này.

## 7. Chạy đúng một sample Stock Alert

Dùng `run-baseline` thay vì gọi thẳng `src.collect` với task config. Runner sẽ
override `max_examples`, `n_responses`, `rollout_version`, đồng thời bỏ
`agent_file` cũ nếu manifest không yêu cầu custom agent.

```bash
task_id="woocommerce_stock_alert_s2l"
model_name="qwen3.6-35b-a3b-fp8"
run_stamp="$(date +%Y%m%d-%H%M%S)"
rollout_version="stock_alert_one_$run_stamp"
batch_dir="generated/baseline_batches/$rollout_version"

manifest=$(cat <<EOF
task_id,model_name,max_examples,rollout_version,n_responses
$task_id,$model_name,1,$rollout_version,1
EOF
)

printf '%s\n' "$manifest" | uv run python run.py run-baseline \
  --manifest - \
  --batch-dir "$batch_dir" \
  --yes
```

Hai số `1` có ý nghĩa khác nhau:

- `max_examples=1`: chỉ lấy sample đầu tiên trong dataset.
- `n_responses=1`: chỉ chạy một rollout cho sample đó.

Timestamp tạo một rollout và batch directory mới, tránh `resume: true` tái sử
dụng kết quả của lần chạy cũ.

Các artifact chính:

```text
generated/baseline_batches/<rollout_version>/raw_manifest.txt
generated/baseline_batches/<rollout_version>/configs/
generated/baseline_batches/<rollout_version>/logs/
results/woocommerce_stock_alert_s2l/<model>_default/rollouts/<rollout_version>/run.json
results/woocommerce_stock_alert_s2l/<model>_default/rollouts/<rollout_version>/eval_results.yaml
```

Theo dataset hiện tại, `max_examples=1` chọn record đầu tiên,
`woocommerce_stock_alert_s2l_0`.

## 8. Chạy baseline cho một benchmark bất kỳ

Trước tiên mở `tasks/<task_id>/run.yaml` và xác nhận:

- `data_path` tồn tại.
- `prompt_name` tồn tại dưới `tasks/<task_id>/prompts/`.
- Alias `model_name` có trong `configs/models.yaml`.
- Docker image trong `server_image` đã được build nếu `use_docker: true`.
- Mọi dependency hoặc server riêng của benchmark đã được chuẩn bị.

Đếm số record trong dataset JSON, ví dụ:

```bash
data_path="data/woocommerce_stock_alert_s2l.json"
example_count=$(uv run python -c 'import json, sys; print(len(json.load(open(sys.argv[1], encoding="utf-8"))))' "$data_path")
printf '%s\n' "$example_count"
```

Sau đó chạy toàn bộ dataset:

```bash
task_id="woocommerce_stock_alert_s2l"
model_name="qwen3.6-35b-a3b-fp8"
max_examples=$example_count
n_responses=1
run_stamp="$(date +%Y%m%d-%H%M%S)"
rollout_version="full_$run_stamp"
batch_dir="generated/baseline_batches/${task_id}_$rollout_version"

manifest=$(cat <<EOF
task_id,model_name,max_examples,rollout_version,n_responses
$task_id,$model_name,$max_examples,$rollout_version,$n_responses
EOF
)

printf '%s\n' "$manifest" | uv run python run.py run-baseline \
  --manifest - \
  --batch-dir "$batch_dir" \
  --yes
```

Để chạy benchmark khác, thay `$task_id`, `$model_name`, `$data_path` và build đúng
Docker service. Với replication paper, số rollout thường là `3`; với smoke test
nên giữ `1` để giảm thời gian và chi phí.

### Dùng custom/adapted agent

Baseline manifest có thể nhận thêm cột `agent_file`:

```csv
task_id,model_name,max_examples,rollout_version,n_responses,prompt_name,agent_file
woocommerce_stock_alert_s2l,qwen3.6-35b-a3b-fp8,100,adapted_full,1,default,results/.../best_config.py
```

Đường dẫn phải tồn tại. Nếu không cung cấp `agent_file`, `run-baseline` sử dụng
agent mặc định của task và bỏ đường dẫn agent cũ trong template `run.yaml`.

## 9. Chạy GEPA optimization (tùy chọn)

GEPA không phải là baseline inference. Nó tối ưu harness trên train/validation,
sau đó chạy harness tốt nhất. Cần cấu hình cả `model_name` và `reflection_lm`
trong model registry.

Ví dụ smoke test với một task:

```bash
run_stamp="$(date +%Y%m%d-%H%M%S)"
batch_dir="generated/gepa_batches/stock_alert_$run_stamp"

manifest=$(cat <<EOF
Task,task_lm,N,budget ($),use_adaptation,reflection_lm,num_exploration,seed
woocommerce_stock_alert_s2l,qwen3.6-35b-a3b-fp8,10,2,TRUE,<reflection-model-alias>,1,0
EOF
)

printf '%s\n' "$manifest" | uv run python run.py run \
  --manifest - \
  --batch-dir "$batch_dir" \
  --max-parallel 1 \
  --yes
```

Không dùng `N=1` cho GEPA vì task còn phải chia train/validation. Điều chỉnh
budget và `N` theo thí nghiệm cần tái lập.

## 10. Chạy lại batch đã prepare

Có thể tách bước tạo config và bước thực thi để kiểm tra YAML trước khi chạy:

```bash
printf '%s\n' "$manifest" | uv run python run.py prepare-baseline \
  --manifest - \
  --batch-dir "$batch_dir"

cat "$batch_dir"/configs/*.yaml

uv run python run.py launch-baseline \
  --batch-dir "$batch_dir" \
  --yes
```

Đây cũng là cách phù hợp khi cần chỉnh một config sinh ra mà không thay đổi
template chung của task.

## 11. Lỗi thường gặp

### `Permission denied (publickey)` khi clone submodule

Dùng HTTPS override như phần 2 hoặc cấu hình GitHub SSH key có quyền truy cập.

### `USER_UID not set`

```bash
container_uid="$(id -u)"
if [ "$container_uid" -eq 0 ]; then container_uid=1000; fi
```

Sau đó dùng `env UID="$container_uid" docker compose build <service>` trong cùng shell.

### `Unknown model_name` hoặc `Unknown eval_lm_name`

Thêm đúng alias vào `configs/models.yaml`. Với evaluator rule-based, bỏ
`eval_lm` khỏi config nếu không cần.

### Model server chạy local nhưng container không kết nối được

Trong `.env`, dùng IP host truy cập được từ container trên Ubuntu, hoặc
`host.docker.internal` trên Docker Desktop Windows; xem phần 4.

### Batch directory đã tồn tại

Runner không ghi đè batch directory. Dùng timestamp/rollout version mới hoặc
chọn một directory mới.

### Lần chạy mới lại dùng kết quả cũ

Task config thường có `resume: true`. Dùng `rollout_version` mới để tạo output
directory độc lập.

### `agent_file` không tồn tại

Không gọi trực tiếp task `run.yaml` có đường dẫn agent từ thí nghiệm cũ. Dùng
`run-baseline` và không thêm cột `agent_file`, hoặc cung cấp đường dẫn custom
agent thực sự tồn tại.

### MCP server báo `os error 2` hoặc thiếu `Server.list_tools`/`call_tool`

Hai lỗi này lần lượt đến từ việc đường dẫn Linux `/workspace/...` bị Windows
chuyển thành đường dẫn ổ đĩa, và LOCA không tương thích với MCP 2.x. Repository
đã giữ nguyên đường dẫn POSIX khi chạy Docker và pin `mcp>=1.9.0,<2`.

Sau khi cập nhật code, đồng bộ lock/dependency rồi build lại image bằng cache:

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
container_uid="$(id -u)"
if [ "$container_uid" -eq 0 ]; then container_uid=1000; fi

uv lock
uv sync
env UID="$container_uid" docker compose build woocommerce_stock_alert_s2l
```

Không cần `--no-cache`. Các sample sau dùng chung image
`woocommerce_stock_alert_s2l:latest`; chỉ cần build lại khi dependency hoặc
Dockerfile thay đổi.

### `uv` không truy cập được cache mặc định trên Windows

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
```

Sau đó chạy lại `uv sync` hoặc `uv run ...` trong cùng Bash session.

## 12. Bản chạy trên Windows Git Bash

Mở **Git Bash**, chuyển tới thư mục checkout, và bật Docker Desktop ở chế độ
Linux containers. Các lệnh Bash ở phần 2–11 dùng được sau khi thiết lập sau;
không dùng cú pháp `$env:...`, `Get-Date` hoặc dấu backtick của PowerShell.

Cài `uv` bản Windows nếu chưa có bằng lệnh sau ngay trong Git Bash
(lệnh này gọi PowerShell để chạy installer chính thức):

```bash
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
export PATH="$(cygpath -u "$USERPROFILE")/.local/bin:$PATH"
uv --version
```

Lệnh `export PATH` giúp dùng `uv` ngay trong Git Bash hiện tại với vị trí cài
mặc định của Windows. Sau đó tiếp tục setup:

```bash
# Thay đường dẫn bằng checkout trên máy Windows.
cd /c/Users/YOUR_USER/slm-harness-adaptation-reproduce
export UV_CACHE_DIR="$(pwd -W)/.uv-cache"
export MSYS_NO_PATHCONV=1
export MSYS2_ENV_CONV_EXCL=UV_CACHE_DIR
container_uid=1000

git --version
uv --version
docker info
git submodule sync --recursive
git submodule update --init --recursive
uv sync
mkdir -p configs
```

`pwd -W` tạo đường dẫn Windows cho `uv` native. `MSYS_NO_PATHCONV=1` giữ nguyên
đường dẫn container như `/workspace/...` khi Git Bash gọi Docker CLI.
Nếu submodule SSH lỗi, dùng HTTPS override ở phần 2. Cấu hình
`configs/models.yaml` và `.env` như phần 4; model server trên Windows host dùng
`QWEN_API_BASE=http://host.docker.internal:8000/v1`.

Build và chạy thử đúng một sample trong cùng Git Bash session:

```bash
test -e .vertex-ai.json || printf '%s\n' '{}' > .vertex-ai.json
uv run python -c "from src.utils import LM_DICT; print(sorted(LM_DICT))"
env UID=1000 docker compose build woocommerce_stock_alert_s2l

run_stamp="$(date +%Y%m%d-%H%M%S)"
rollout_version="stock_alert_one_${run_stamp}"
uv run python run.py run-baseline \
  --manifest - \
  --batch-dir "generated/baseline_batches/$rollout_version" \
  --yes <<EOF
task_id,model_name,max_examples,rollout_version,n_responses
woocommerce_stock_alert_s2l,qwen3.6-35b-a3b-fp8,1,$rollout_version,1
EOF
```

Để chạy bốn benchmark ở phần 13, giữ `container_uid=1000` và cache Windows
đã đặt ở đây; bỏ qua block khởi tạo shell Ubuntu ở phần 13.1. Lệnh heredoc,
`date`, `printf` và các lệnh runner còn lại dùng chung trên cả hai shell.

## 13. Chạy Stock Alert, Anomaly Detection, Website Management và Code Refactoring

Bốn task dùng cùng agent model `qwen3.5-9b`, reflection model
`qwen3.6-35b-a3b-fp8` và GEPA seed `42`. Các file cấu hình liên quan là
`run.yaml` và `gepa_optimize.yaml` trong thư mục của từng task; repository không
có file tên `gepa_optimized.yaml`.

Phân hoạch dữ liệu mặc định không chồng lấn:

| Benchmark | Task ID | Tổng sample | Test | GEPA train/validation |
|---|---|---:|---:|---:|
| Stock Alert | `woocommerce_stock_alert_s2l` | 100 | index 0–29 | index 30–59: 15/15 |
| Anomaly Detection | `machine_operating_s2l` | 100 | index 0–29 | index 30–59: 15/15 |
| Website Management | `webarena` | 50 | index 0–29 | index 30–49: 10/10 |
| Code Refactoring | `refactorbench` | 100 | index 0–29 | index 30–59: 15/15 |

`max_examples: 30` là giới hạn tối đa sau `data_start_index: 30`. Vì dataset
WebArena chỉ có 50 sample, GEPA tự lấy 20 sample còn lại thay vì 30. Mỗi lần
đánh giá held-out sau GEPA dùng `test_n_responses: 3`; baseline bên dưới dùng
một response cho mỗi sample.

### 13.1. Chuẩn bị chung trên Ubuntu/Git Bash

Chạy từ thư mục gốc của repository, sau khi hoàn thành phần 1–5.
Trên Ubuntu, khởi tạo shell như sau (Git Bash dùng phần 12):

```bash
export UV_CACHE_DIR="$PWD/.uv-cache"
container_uid="$(id -u)"
if [ "$container_uid" -eq 0 ]; then container_uid=1000; fi
```

Sau đó chạy chung trên cả hai shell:

```bash
uv sync

uv run python - <<'PY'
from src.utils import LM_DICT

required = {"qwen3.5-9b", "qwen3.6-35b-a3b-fp8"}
missing = required - LM_DICT.keys()
assert not missing, f"Missing model aliases: {sorted(missing)}"
print("Model aliases OK")
PY

env UID="$container_uid" docker compose build \
  woocommerce_stock_alert_s2l \
  machine_operating_s2l \
  webarena \
  refactorbench
```

#### Chuẩn bị source code cho RefactorBench

`data/refactorbench.json` giữ các `repo_path` tuyệt đối từ máy tạo dataset, ví
dụ `/mnt/data_4tb/datasets/RefactorBench/repositories/salt_refactor`. Các đường
dẫn đó không tồn tại trên máy mới. Clone bản benchmark chính thức, chứa đúng
snapshot của chín source repository, rồi dùng `REFACTORBENCH_REPOS_DIR` để
override `repo_path`:

```bash
mkdir -p external

if [ ! -d external/RefactorBench/.git ]; then
  git clone --depth 1 https://github.com/microsoft/RefactorBench.git \
    external/RefactorBench
fi

export REFACTORBENCH_REPOS_DIR="$PWD/external/RefactorBench/repositories"

uv run python - <<'PY'
import json
import os
from pathlib import Path

data = json.loads(Path("data/refactorbench.json").read_text(encoding="utf-8"))
repo_root = Path(os.environ["REFACTORBENCH_REPOS_DIR"])
repo_names = sorted({item["repo_name"] for item in data})
missing = [name for name in repo_names if not (repo_root / name).is_dir()]
assert not missing, f"Missing RefactorBench repositories: {missing}"
print("RefactorBench repositories OK")
PY
```

Biến `REFACTORBENCH_REPOS_DIR` phải tồn tại trong chính shell session dùng để
prepare hoặc launch batch. Khi mở terminal mới, chạy lại lệnh `export`; không
cần sửa `data/refactorbench.json`. Nếu collect đã fail vì thiếu repository, sau
khi export đúng biến có thể tiếp tục batch đã prepare:

```bash
uv run python run.py launch-baseline \
  --batch-dir generated/baseline_batches/REPLACE_WITH_PREPARED_BATCH \
  --yes
```

#### Chuẩn bị website cho WebArena

WebArena đang có `start_servers: false`, vì vậy khởi động site
`shopping_admin` trước khi chạy baseline hoặc GEPA. Lần đầu lệnh này tải image;
những lần sau nó dùng lại image/container local:

```bash
uvx webarena-verified env start --site shopping_admin
```

Lệnh start tạo container Docker chạy nền, nên không cần giữ terminal mở. Nếu
CLI trên máy vẫn giữ foreground, có thể tách nó khỏi terminal và lưu log:

```bash
nohup uvx webarena-verified env start --site shopping_admin \
  > /tmp/webarena-shopping-admin.log 2>&1 &
disown
```

Xác minh container và trang admin bằng HTTP `GET` trước khi launch benchmark:

```bash
docker ps --format '{{.Names}} {{.Status}} {{.Ports}}' \
  | grep webarena_verified_shopping_admin

curl -sS -o /dev/null \
  -w 'status=%{http_code}\n' \
  http://localhost:7780/admin
```

Kết quả cần có container ở trạng thái `Up` và `status=200`. Không dùng
`curl -I` để health-check Magento vì request `HEAD` có thể trả `404` dù request
`GET` hoạt động. Khi collect bắt đầu, harness kết nối site và agent container
vào `webarena-net`, rồi thay `__SHOPPING_ADMIN__` bằng URL nội bộ dạng
`http://<container-ip>/admin`. Có thể kiểm tra trong trace/log:

```text
Starting URL: http://<container-ip>/admin
```

Nếu trace vẫn chứa nguyên `Starting URL: __SHOPPING_ADMIN__`, dừng rollout,
khởi động/kiểm tra site và chạy lại với `rollout_version` mới. Việc khởi động
site giữa một rollout không sửa prompt đã được preprocess.

### 13.2. Chạy baseline trên 30 test sample đầu

Command sau tạo một batch tuần tự cho cả bốn benchmark. `run-baseline` bỏ mọi
`agent_file` cũ trong template nếu manifest không truyền cột đó, nên đây là
baseline bằng agent mặc định.

```bash
run_stamp="$(date +%Y%m%d-%H%M%S)"
rollout_version="four_benchmarks_test30_${run_stamp}"
batch_dir="generated/baseline_batches/${rollout_version}"

uv run python run.py run-baseline \
  --manifest - \
  --batch-dir "$batch_dir" \
  --yes <<EOF
task_id,model_name,max_examples,rollout_version,n_responses,prompt_name
woocommerce_stock_alert_s2l,qwen3.5-9b,30,$rollout_version,1,default
machine_operating_s2l,qwen3.5-9b,30,$rollout_version,1,default
webarena,qwen3.5-9b,30,$rollout_version,1,shopping_admin
refactorbench,qwen3.5-9b,30,$rollout_version,1,default
EOF
```

Muốn chạy riêng một benchmark thì giữ header và đúng một data row trong
heredoc. Baseline runner luôn chạy các row tuần tự.

### 13.3. Chạy GEPA rồi đánh giá best harness trên held-out test

Command này giữ budget mặc định của từng task, chạy seed 42 và giới hạn một
pipeline tại một thời điểm để tránh bốn benchmark cùng tranh GPU/Docker. Giá trị
`max_examples=30` trong manifest chỉ override kích thước GEPA; các trường
`data_start_index`, `test_start_index` và `test_max_examples` vẫn lấy từ từng
`gepa_optimize.yaml`.

```bash
run_stamp="$(date +%Y%m%d-%H%M%S)"
batch_dir="generated/gepa_batches/four_benchmarks_seed42_${run_stamp}"

uv run python run.py run \
  --manifest - \
  --batch-dir "$batch_dir" \
  --max-parallel 1 \
  --yes <<'EOF'
task_id,model_name,max_examples,max_cost,use_adaptation_guide,reflection_lm,prompt_name,seed,num_exploration
woocommerce_stock_alert_s2l,qwen3.5-9b,30,16,true,qwen3.6-35b-a3b-fp8,default,42,1
machine_operating_s2l,qwen3.5-9b,30,10,true,qwen3.6-35b-a3b-fp8,default,42,1
webarena,qwen3.5-9b,30,8,true,qwen3.6-35b-a3b-fp8,shopping_admin,42,1
refactorbench,qwen3.5-9b,30,20,true,qwen3.6-35b-a3b-fp8,default,42,1
EOF
```

`run.py run` thực hiện đủ ba phase: optimize trên train/validation, collect best
harness trên 30 held-out test sample đầu, rồi evaluate. Không cần gọi riêng
`src.gepa_optimize`, `src.collect` hoặc `src.evaluate`.

Một seed chỉ có một output canonical tại
`results/<task>/<model>_<prompt>/gepa/seed42`. Nếu seed 42 đã được prepare hoặc
chạy trước đó, không tạo batch mới với cùng task/model/prompt/seed. Tiếp tục batch
đã prepare bằng:

```bash
gepa_batch_dir="generated/gepa_batches/REPLACE_WITH_PREPARED_BATCH"

uv run python run.py launch \
  --batch-dir "$gepa_batch_dir" \
  --max-parallel 1 \
  --yes
```

Nếu optimize đã hoàn tất nhưng phase test/evaluate bị gián đoạn, chạy lại trực
tiếp hai phase còn thiếu bằng `post_gepa_run.yaml` của task tương ứng, ví dụ:

```bash
post_config="results/woocommerce_stock_alert_s2l/qwen3.5-9b_default/gepa/seed42/shared/config/post_gepa_run.yaml"

uv run python -m src.collect --config "$post_config"
uv run python -m src.evaluate --config "$post_config"

uv run python run.py resume-post \
  --batch-dir "$gepa_batch_dir"
```

Lệnh `resume-post` cuối cùng chỉ dựng lại summary/table từ artifact đã có; nó
không tự chạy lại collect hoặc evaluate. Thay phần đầu của `post_config` bằng
task/prompt tương ứng cho ba benchmark còn lại.

Các vị trí cần kiểm tra:

```text
generated/gepa_batches/<batch>/batch.json
generated/gepa_batches/<batch>/launcher.log
generated/gepa_batches/<batch>/logs/
results/<task>/<model>_<prompt>/gepa/seed42/shared/config/used_config.yaml
results/<task>/<model>_<prompt>/gepa/seed42/shared/config/optimization_summary.json
results/<task>/<model>_<prompt>/rollouts/gepa_seed42_best_best_config/eval_results.yaml
```
