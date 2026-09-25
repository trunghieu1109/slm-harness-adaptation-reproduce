# Tổng quan bốn benchmark task

Tài liệu này mô tả bốn task đang được quan tâm:

- `woocommerce_stock_alert_s2l` — Stock Alert
- `machine_operating_s2l` — Anomaly Detection
- `webarena` — Website Management
- `refactorbench` — Code Refactoring

Mục tiêu là phân biệt rõ input của sample, môi trường được setup, tool agent
được cấp, artifact đầu ra và cách evaluator tính điểm.

## Luồng chung

```text
Dataset record -> setup_workspace() -> tạo môi trường riêng
-> chạy agent -> gọi tool/tạo artifact -> evaluator -> score
```

Kết quả thường nằm tại `results/<task>/<model>_<prompt>/rollouts/<version>/`:

```text
run.json                 # conversation, tool calls, metrics, errors
eval_results.yaml        # score và feedback
example0_rollout0/       # workspace/artifact cuối
example0_rollout0_logs/  # trace JSON/Markdown
```

---

## 2. Anomaly Detection

### Source code

- Config: `tasks/machine_operating_s2l/run.yaml`
- Dataset: `data/machine_operating_s2l.json`
- Prompt: `tasks/machine_operating_s2l/prompts/default.md`
- Setup: `src/task_setups/machine_operating_s2l.py`
- Evaluator: `src/task_evals/machine_operating_s2l.py`

### Input và môi trường

Một sample có các tham số như `seed`, `hours`, `interval_minutes`,
`anomaly_rate`, `total_machines` và `total_sensors`. Ví dụ sample đầu dùng
`42, 4 giờ, interval 5 phút, anomaly_rate 0.1, 5 machines và 4 sensors`.

Setup tạo mock Google Cloud database ở `local_db/google_cloud/`, với BigQuery
project `local-project`, dataset `machine_operating` và table `live_sensor`.
Agent được đặt file `machine_operating_parameters.xlsx`, chứa min/max bình
thường cho từng cặp `machine_id + sensor_type`.

Ground truth bị giấu tại `example0_rollout0_logs/groundtruth/anomaly_report.csv`.

### Luồng agent và tool

Agent dùng Google Cloud MCP để:

1. Query `machine_operating.live_sensor`.
2. Lọc thời gian `2025-08-19 11:30` đến `12:30`.
3. Đọc Excel bằng terminal/Python.
4. Join sensor data với parameter theo machine và sensor.
5. Đánh dấu anomaly nếu reading ngoài min/max.
6. Tạo `anomaly_report.csv`.
7. Tạo bucket `iot_anomaly_reports` nếu cần.
8. Upload CSV vào bucket.

Ngoài MCP, agent có `terminal`, `file_editor`, `think` và `finish`.

### Output và đánh giá

Artifact quan trọng là object `anomaly_report*.csv` trong bucket
`iot_anomaly_reports`. CSV dự kiến có các cột `timestamp`, `machine_id`,
`sensor_type`, `reading` và `normal_range`.

Evaluator kiểm tra bucket, file CSV, khả năng parse và các cột bắt buộc
`timestamp`, `machine_id`, `sensor_type`, `reading`. Sau đó so sánh với ground
truth: timestamp tolerance 60 giây, reading tolerance 0.01, còn machine ID và
sensor type phải trùng. Precision và recall đều phải đạt ít nhất 95%; pass là
`1.0`, fail là `0.0`. Prompt yêu cầu `normal_range`, dù evaluator hiện chưa
dùng giá trị cột đó trong phép so sánh.

---

## 3. Website Management — WebArena

### Source code và phạm vi hiện tại

- Config: `tasks/webarena/run.yaml`
- Dataset: `data/webarena_shopping_admin_easy.json`
- Prompt: `tasks/webarena/prompts/shopping_admin.md`
- Setup/server: `src/task_setups/webarena_servers.py`
- Evaluator: `src/task_evals/webarena.py`

WebArena tổng quát có thể có nhiều site, nhưng config hiện tại chạy 50 sample
trên `shopping_admin`.

### Input

Một record gồm site, task ID, trạng thái login, start URL, prompt và eval
reference answer. Ví dụ prompt là `What is the top-1 best-selling product in
2022`, với reference answer `Quest Lumaflex™ Band`.

Placeholder `__SHOPPING_ADMIN__` được đổi thành URL thật. Agent được cấp
BrowserToolSet và browser session đăng nhập; prompt có login dự phòng
`admin/admin1234`.

### Dịch vụ và luồng agent

WebArena cần web application server chạy bằng Docker/network `webarena-net`.
Agent dùng browser để mở dashboard, click, nhập form, đọc bảng, filter dữ liệu,
tìm product/order/customer/review và thực hiện mutate nếu prompt yêu cầu.

Task có thể thuộc `RETRIEVE`, `MUTATE` hoặc `NAVIGATE`. Bộ
`shopping_admin_easy` hiện chủ yếu là retrieval, nhưng có cả yêu cầu về hành
động không được hỗ trợ; agent phải báo đúng lỗi thay vì bịa kết quả.

### Output và đánh giá

Agent phải trả structured JSON, ví dụ có các key `task_type`, `status`,
`retrieved_data` và `error_details`. Evaluator có thể extract `ANSWER: ...`,
fenced JSON hoặc các key `retrieved_data`, `answer`, `result`, `value`,
`error_details`.

Dataset hiện tại có 50/50 `string_match`: 10 `exact_match`, 29 `must_include`
và 11 `fuzzy_match`.

- `exact_match`: normalized answer phải trùng hoàn toàn.
- `must_include`: phải chứa mọi cụm bắt buộc.
- `fuzzy_match`: evaluator LM kiểm tra tương đương ngữ nghĩa.
- Task không khả thi: phải nhận diện đúng và giải thích.

Các eval type `url_match`/`program_html` của WebArena tổng quát chưa được chấm
offline trong evaluator hiện tại và có thể trả `score: null`; chúng không xuất
hiện trong dataset shopping admin hiện tại.

---

## 4. Code Refactoring — RefactorBench

### Source code

- Config: `tasks/refactorbench/run.yaml`
- Dataset: `data/refactorbench.json`
- Prompt: `tasks/refactorbench/prompts/default.md`
- Setup: `src/task_setups/refactorbench.py`
- Evaluator: `src/task_evals/refactorbench.py`

### Input và workspace

Một record có `id`, `repo_name`, `repo_path`, `problem_statement`, `prompt` và
`eval_script`. Setup copy source repository vào workspace và thêm
`task_context.json`. Không có WooCommerce, Cloud hay browser service; input
chính là repository cần sửa.

Dataset 100 bài trải trên Django, Salt, Scrapy, Celery, Ansible, Requests,
Tornado, FastAPI và Flask.

### Luồng agent và output

Agent dùng `terminal`, `file_editor`, `think` và `finish`. Các yêu cầu thường
gặp là rename function/constant, cập nhật import và call site, thêm parameter,
di chuyển function, merge module, tạo abstraction, xóa/rename file và sửa test.

Ví dụ một sample yêu cầu thêm `log=False` vào `delete_directory`, truyền
`log=True` trong `cloud.py` và giữ `False`/mặc định trong test.

Output quan trọng là source tree sau sửa: file đúng được sửa, file mới tạo/file
cũ xóa đúng, import/call site cập nhật và symbol cũ không còn ngoài phạm vi
cho phép. Câu trả lời cuối gần như không quyết định điểm.

### Đánh giá

Mỗi record chứa deterministic Python `unittest` script trong `eval_script`.
Evaluator ghi script vào thư mục tạm, chạy bằng Python 3.11 và timeout 60 giây.

```text
Tất cả test pass -> score 1.0
Bất kỳ test fail -> score 0.0
Timeout          -> score 0.0
```

Test có thể dùng AST và filesystem để kiểm tra signature, default parameter,
imports, call arguments, symbol, file tồn tại/xóa và `__all__`. `eval_lm` nếu có
chỉ dùng để rút gọn thông báo lỗi, không quyết định pass/fail.

---

## Bảng so sánh

| Task | Môi trường | Tool chính | Artifact được chấm | Kiểu chấm |
|---|---|---|---|---|
| Stock Alert | Local WooCommerce/Sheets/Email JSON DB | 3 MCP + terminal | Sheet rows + Sent emails | Cả hai component phải pass |
| Anomaly Detection | Mock BigQuery/Cloud Storage + Excel | Google Cloud MCP + terminal | CSV trong bucket | Precision/recall >= 95% |
| Website Management | Shopping Admin web app | BrowserToolSet | Structured answer/web state | Exact/include/fuzzy |
| Code Refactoring | Source repository copy | Terminal + file editor | Source tree | Deterministic unittest |

## Đọc performance đúng cách

Không chỉ nhìn process exit code. Nên đọc `run.json` để biết tool/error, đọc
`trace_*.md` để biết thứ tự thao tác, kiểm tra artifact trong workspace/mock
service, rồi đọc `eval_results.yaml` để biết score. Launcher có thể báo
`completed` chỉ vì collect/evaluate process đã kết thúc; điều đó không đảm bảo
agent hoàn thành nghiệp vụ hay đạt điểm cao.

## 1. Stock Alert

### Source code

- Config: `tasks/woocommerce_stock_alert_s2l/run.yaml`
- Dataset: `data/woocommerce_stock_alert_s2l.json`
- Prompt: `tasks/woocommerce_stock_alert_s2l/prompts/default.md`
- Setup: `src/task_setups/woocommerce_stock_alert_s2l.py`
- Evaluator wrapper: `src/task_evals/woocommerce_stock_alert_s2l.py`
- Evaluator chi tiết: `LOCA-bench/gem/envs/woocommerce_stock_alert_s2l/evaluation/evaluate_updated_stock_alert.py`

### Input

Một sample có dạng:

```json
{
  "id": "woocommerce_stock_alert_s2l_0",
  "seed": 42,
  "num_low_stock": 3,
  "num_normal_stock": 5,
  "prompt": "..."
}
```

Setup dùng `seed` và hai số lượng để tạo catalog mock. Sample đầu tiên có 3
low-stock và 5 normal-stock, tổng cộng 8 sản phẩm.

### Cách sinh WooCommerce mock

Đây không phải WooCommerce thật. LOCA-bench tạo local JSON database rồi expose
nó qua WooCommerce MCP server. Generator gọi `random.seed(seed)`, chọn template
sản phẩm bằng `random.choices`, rồi tạo các trường:

```text
id, sku, name, category, price,
stock_quantity, stock_threshold, supplier
```

Với `i <= num_low_stock`, generator đảm bảo `stock_quantity < stock_threshold`.
Các sản phẩm còn lại có `stock_quantity >= stock_threshold`.

SKU là Stock Keeping Unit, mã định danh sản phẩm trong kho. SKU có dạng:

```text
<3 ký tự đầu category viết hoa>-<số thứ tự 5 chữ số>
```

Ví dụ `ELE-00002` có thể là mã của sản phẩm thứ hai thuộc Electronics.

Cùng seed và cùng số lượng sẽ tái tạo cùng dữ liệu nếu generator không đổi.
Cùng số lượng nhưng khác seed sẽ tạo catalog, SKU, stock, threshold và supplier
khác. Dataset dùng các seed tuần tự nên sample 0 và sample 10 có thể cùng 3
low + 5 normal nhưng không phải cùng sản phẩm.

Ví dụ sample seed 42:

| ID | SKU | Tên | Stock | Threshold | Loại |
|---:|---|---|---:|---:|---|
| 1 | `HOM-00001` | Microwave #1 | 2 | 20 | Low-stock |
| 2 | `ELE-00002` | Smart Watch #2 | 1 | 39 | Low-stock |
| 3 | `FUR-00003` | Bed Frame #3 | 34 | 42 | Low-stock |
| 4 | `ELE-00004` | E-Reader #4 | 63 | 28 | Normal |

### Workspace và dịch vụ

Trước khi agent chạy, setup tạo:

```text
example0_rollout0/
├── admin_credentials.txt
├── purchasing_manager_email.txt
├── stock_alert_email_template.md
└── local_db/
    ├── woocommerce/
    ├── google_sheets/
    └── emails/
```

- `admin_credentials.txt`: email/password tài khoản admin mock.
- `purchasing_manager_email.txt`: địa chỉ purchasing manager.
- `stock_alert_email_template.md`: subject/body email mẫu.

Đây đều là local JSON database:

```text
local_db/woocommerce/*.json
local_db/google_sheets/*.json
local_db/emails/users.json
local_db/emails/users_data/<account>/*.json
```

Trong Docker, workspace được mount thành `/workspace/project`; MCP server nhận
`WOOCOMMERCE_DATA_DIR`, `GOOGLE_SHEET_DATA_DIR` và `EMAIL_DATA_DIR`. Không có
request thật tới Google Sheets hoặc mail server.

Ground truth nằm ngoài workspace agent:

```text
example0_rollout0_logs/groundtruth/task_artifacts/
├── preprocess/woocommerce_products.json
├── files/sheet_id.txt
└── agent_workspace/
```

Google Sheet được tạo tên `WooCommerce Stock Alert`, sheet con `Stock Alert`,
header và một low-stock example row. Email database tạo user
`admin@woocommerce.local` với các folder rỗng.

### Tool và luồng agent

Ở mức logic có ba MCP server: WooCommerce, Google Sheets và Email. Trace hiện
expose 100 MCP functions:

```text
55 WooCommerce + 15 Google Sheets + 30 Email
```

Cộng thêm `terminal`, `file_editor`, `think`, `finish`. Tool nghiệp vụ chính:

```text
woocommerce_woo_products_list / _get
google_sheet_list_spreadsheets / _list_sheets / _get_sheet_data
google_sheet_add_rows / _update_cells
email_login / email_get_current_user / email_send_email
```

MCP cũng expose CRUD cho orders, customers, coupons, shipping, reports,
settings và webhooks dù task không cần phần lớn số đó.

Luồng đúng:

```text
Đọc credentials/template -> list products -> đọc stock/threshold
-> lọc stock_quantity < stock_threshold -> tìm sheet
-> ghi tất cả low-stock -> login email -> gửi từng email -> finish
```

### Output và đánh giá

Output được chấm là trạng thái database cuối, không phải prose cuối của model.

Sheet phải có đủ low-stock, không có normal-stock, đúng SKU/tên/current stock/
safety threshold và các cột bắt buộc không rỗng:

```text
Product ID, Product Name, SKU, Current Stock,
Safety Threshold, Supplier Name, Supplier ID, Supplier Contact
```

Email phải nằm trong Sent, gửi tới `laura_thompson@mcp.com`, có ít nhất một
email cho mỗi low-stock, chứa SKU tương ứng, subject có dấu hiệu stock alert,
body có greeting/purchasing manager và thông tin stock/threshold, không còn
placeholder như `{google_sheets_link}`.

“Gần đúng template” nghĩa là không cần giống từng ký tự nhưng phải giữ các
điều kiện nội dung trên. Hai component là điểm nhị phân:

```text
Sheet pass + Email pass -> score 1.0
Một trong hai fail       -> score 0.0
```

---
