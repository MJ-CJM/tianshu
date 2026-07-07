# Python 单元测试设计指南

---

## 一、Fixture 设计

Fixture 是 pytest 框架中最核心的测试基础设施之一，它负责为测试用例提供可复用的前置状态与资源。好的 fixture 设计能显著降低测试代码的重复率，同时提升可维护性。

### 1.1 基础 fixture 与 scope 选择

pytest 提供四种 scope：`function`（默认，每个测试函数执行一次）、`class`（每个测试类执行一次）、`module`（每个模块执行一次）、`session`（整个测试会话执行一次）。合理选择 scope 是 fixture 设计的第一步，不当的 scope 要么浪费资源，要么引入测试间状态耦合。

```python
import pytest
import sqlite3

# scope="module"：整个模块共享一个数据库连接，避免重复初始化
@pytest.fixture(scope="module")
def db_connection():
    """模块级 fixture：创建一个临时数据库并建表，模块结束后清理。"""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT, age INTEGER)")
    yield conn
    conn.close()

# scope="function"：每个测试函数获得独立行级数据，互不干扰
@pytest.fixture
def sample_user(db_connection):
    """函数级 fixture：插入一条用户记录，测试结束后回滚。"""
    cursor = db_connection.cursor()
    cursor.execute("INSERT INTO users (name, age) VALUES ('Alice', 30)")
    db_connection.commit()
    yield cursor.lastrowid
    db_connection.rollback()  # 每个测试后清理，保证隔离
```

### 1.2 fixture 工厂模式

当测试需要动态创建不同参数的对象时，与其写一组相似的 fixture，不如用一个 fixture 工厂。这比 `@pytest.mark.parametrize` 更灵活——调用方在测试体内按需定制。

```python
from dataclasses import dataclass
from typing import Callable

@dataclass
class Order:
    order_id: str
    items: list[dict]
    paid: bool = False

@pytest.fixture
def order_factory() -> Callable[..., Order]:
    """返回一个工厂函数，允许测试按需构造不同 Order。"""
    def _make(items=None, paid=False):
        return Order(
            order_id=f"ORD-{id(items)}",
            items=items or [{"sku": "DEFAULT", "qty": 1}],
            paid=paid,
        )
    return _make

def test_paid_order_discount(order_factory):
    order = order_factory(items=[{"sku": "A", "qty": 3}], paid=True)
    assert order.paid is True
    assert len(order.items) == 1
```

### 1.3 conftest.py 与层级共享

pytest 的 `conftest.py` 按目录层级自动加载：根目录的 `conftest.py` 定义全局 fixture，子目录的可覆盖或补充。这是大型项目中组织 fixture 的关键机制。

```
tests/
├── conftest.py          # 全局 fixture（如 app 实例、测试客户端）
├── unit/
│   └── conftest.py      # 单测专用 fixture（如 mock 好的 service）
└── integration/
    └── conftest.py      # 集成测试 fixture（如真实数据库）
```

```python
# tests/conftest.py —— 根级 fixture
import pytest
from myapp import create_app

@pytest.fixture(scope="session")
def app():
    app = create_app(config="testing")
    yield app

@pytest.fixture
def client(app):
    return app.test_client()
```

---

## 二、Mock 策略

Mock 是隔离被测单元与外部依赖的核心手段。但 mock 是把双刃剑：过度 mock 会导致测试与实现细节过度绑定（brittle tests），mock 过少则让单元测试退化为集成测试。策略的关键在于 **明确边界**。

### 2.1 什么该 mock，什么不该 mock

遵循一条铁律：**mock 所有离开进程边界的调用**——网络请求、文件系统、数据库、外部服务、时钟。不 mock 纯函数、数据结构转换和领域逻辑。

```python
# ❌ 不推荐：mock 了内部实现细节
def test_order_total():
    with patch("orders.models.Order._calc_tax") as mock_tax:
        mock_tax.return_value = 5.0
        order = Order(items=[...])
        assert order.total == 105.0  # 绑定到私有方法，重构就挂

# ✅ 推荐：mock 外部依赖，内部逻辑原样测
def test_order_confirmation_email(http_client_mock):
    """只 mock HTTP 调用，核心业务逻辑完整执行。"""
    http_client_mock.post.return_value.status_code = 200
    result = confirm_order(order_id="42")
    assert result.status == "CONFIRMED"
    http_client_mock.post.assert_called_once_with(
        "/email", json={"to": "user@example.com", "template": "order_confirmed"}
    )
```

### 2.2 Mock 与依赖注入

与其在测试中到处 `patch`，不如在代码设计阶段就采用依赖注入。这不仅让 mock 更干净，也倒逼出更模块化的架构。

```python
import httpx
from typing import Protocol

# 定义协议（接口），而非依赖具体实现
class EmailClient(Protocol):
    def send(self, to: str, subject: str, body: str) -> bool: ...

class OrderService:
    def __init__(self, email_client: EmailClient):
        self._email = email_client

    def confirm(self, order_id: str):
        # ...业务逻辑...
        self._email.send(to="user@example.com", subject="Order Confirmed", body="...")

# 测试中注入 mock，无需 patch
from unittest.mock import create_autospec

def test_order_service_sends_email():
    email_mock = create_autospec(EmailClient)
    email_mock.send.return_value = True
    service = OrderService(email_client=email_mock)
    service.confirm("order-1")
    email_mock.send.assert_called_once()
```

### 2.3 Mock 边界与反模式

| 反模式 | 问题 | 正确做法 |
|--------|------|----------|
| `patch` 被测模块内部函数 | 测试与实现耦合 | 仅 mock 进程边界 |
| Mock 返回 mock（链式 mock） | 脆弱、难以阅读 | 使用 `create_autospec` 或手写 stub |
| 不校验 mock 调用参数 | 假阳性——测了个寂寞 | 使用 `assert_called_with` 或 `call_args` |
| Mock 整个数据库 | 掩盖 SQL 错误 | 用 SQLite `:memory:` 做轻量集成测试 |

```python
# ❌ 链式 mock：a.b.c.d.e().f——任何一个环节变化测试就崩
mock_client.config.retry_policy.max_attempts = 3

# ✅ 明确构造返回值对象
response_stub = httpx.Response(status_code=200, json={"ok": True})
mock_client.post.return_value = response_stub
```

---

## 三、参数化测试

参数化让一套测试逻辑覆盖多组输入输出，是提升测试 ROI 最直接的手段。pytest 的 `@pytest.mark.parametrize` 支持单参数、多参数组合以及条件跳过。

### 3.1 基础参数化

```python
import pytest

def is_palindrome(s: str) -> bool:
    cleaned = "".join(c.lower() for c in s if c.isalnum())
    return cleaned == cleaned[::-1]

@pytest.mark.parametrize("text,expected", [
    ("racecar",               True),
    ("A man a plan a canal Panama", True),
    ("hello",                 False),
    ("Was it a car or a cat I saw", True),
    ("",                       True),   # 边界：空串
    ("a",                      True),   # 边界：单字符
    ("ab",                     False),  # 边界：两字符不一致
])
def test_is_palindrome(text, expected):
    assert is_palindrome(text) == expected
```

### 3.2 笛卡尔积参数化

当多个 `parametrize` 装饰器叠加时，pytest 会生成所有组合的笛卡尔积。这对测试交互效应非常有效。

```python
@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
@pytest.mark.parametrize("action", ["read", "write", "delete"])
def test_rbac_permissions(role, action, acl_service):
    """3×3=9 个测试用例，覆盖所有角色×操作组合。"""
    allowed = acl_service.is_allowed(role, action)
    if role == "admin":
        assert allowed  # admin 全权限
    elif role == "editor" and action != "delete":
        assert allowed
    elif role == "viewer":
        assert allowed == (action == "read")
```

### 3.3 间接参数化（indirect）

当参数值需要经过 fixture 加工时，使用 `indirect=True`。这在需要根据参数动态构建复杂测试对象时尤其有用。

```python
from dataclasses import dataclass

@dataclass
class Product:
    sku: str
    unit_price: float
    tax_rate: float

@pytest.fixture
def product(request) -> Product:
    """根据 parametrize 传入的 tuple 构造 Product。"""
    sku, unit_price, tax_rate = request.param
    return Product(sku=sku, unit_price=unit_price, tax_rate=tax_rate)

class TaxCalculator:
    @staticmethod
    def compute(product: Product) -> float:
        return round(product.unit_price * product.tax_rate, 2)

@pytest.mark.parametrize("product,expected_tax", [
    (("SKU-A", 100.0, 0.10), 10.0),
    (("SKU-B", 49.99, 0.05), 2.50),
    (("SKU-C", 0.0, 0.20), 0.0),
], indirect=["product"])
def test_tax_calculation(product, expected_tax):
    assert TaxCalculator.compute(product) == expected_tax
```

### 3.4 通过 `pytest_generate_tests` 动态生成参数

当参数列表需要运行时计算（如从 JSON 文件加载测试用例）时，用 `pytest_generate_tests` 钩子。

```python
# test_contracts.py
def pytest_generate_tests(metafunc):
    if "test_case" in metafunc.fixturenames:
        # 从外部 JSON 加载用例，无需硬编码
        cases = json.loads(Path("test_cases.json").read_text())
        metafunc.parametrize(
            "test_case",
            cases,
            ids=[c["name"] for c in cases],  # 可读的测试 ID
        )

def test_api_contracts(test_case, api_client):
    response = api_client.request(
        method=test_case["method"],
        path=test_case["path"],
        json=test_case.get("body"),
    )
    assert response.status_code == test_case["expected_status"]
```

---

## 四、覆盖率工具

代码覆盖率衡量测试执行期间哪些代码行被触发，但它不是质量指标——只说明代码"被执行过"，不说明"被正确验证过"。将其作为发现测试盲区的探测器，而非考核目标。

### 4.1 `coverage.py` + `pytest-cov`

```bash
pip install coverage pytest-cov
```

```ini
# pyproject.toml 或 setup.cfg
[tool.coverage.run]
branch = true                       # 开启分支覆盖率
source = ["myapp"]                  # 限定统计范围
omit = ["*/tests/*", "*/migrations/*", "*/__init__.py"]

[tool.coverage.report]
precision = 2
skip_covered = false
show_missing = true                 # 终端输出未覆盖行号
exclude_also = [
    "raise NotImplementedError",
    "if TYPE_CHECKING:",
    "class .*\\(Protocol\\):",
]

[tool.coverage.html]
directory = "htmlcov"
```

```bash
# 运行并输出终端覆盖率摘要
pytest --cov=myapp --cov-report=term-missing

# 生成 HTML 报告（可在 CI artifact 中查看）
pytest --cov=myapp --cov-report=html
```

### 4.2 分支覆盖率的价值

行覆盖率容易制造虚假安全感。看这个例子：

```python
def calculate_discount(price: float, member: bool, coupon_code: str | None) -> float:
    if member:
        discount = 0.10
    elif coupon_code:
        discount = 0.05
    else:
        discount = 0.0
    return price * (1 - discount)  # 一行代码，但包含 3 个分支路径
```

单测只测 `member=True` 一行就能拿到 100% 行覆盖，但分支覆盖只有 33%。`--branch` 能暴露这种"假覆盖"。

### 4.3 报告解读与盲区标注

在 CI 中建议设置最低阈值，但不建议设为 100%——追求 100% 覆盖率往往导致大量无价值的"为了覆盖而覆盖"的测试。

```toml
[tool.coverage.report]
fail_under = 80  # 低于 80% 则 CI 失败（可根据项目调整）
```

更重要的是使用 `# pragma: no cover` 标注确实无法/无需测试的代码，并用注释说明原因：

```python
def main() -> None:  # pragma: no cover -- 入口点由集成测试覆盖
    uvicorn.run(app)

class AbstractRepository(ABC):
    @abstractmethod
    def save(self, entity):  # pragma: no cover -- 抽象方法
        ...
```

---

## 五、CI 集成

将测试与覆盖率检查嵌入 CI 流水线，确保每次 PR 自动验证，是把测试从"偶尔跑一下"变成"团队纪律"的关键一步。一套完整的 CI 测试策略应追求三个目标：第一是 **快速反馈**——单元测试应在 2 分钟内完成，让开发者在提交后立刻知道是否破坏了已有功能；第二是 **环境一致性**——CI 中运行的测试环境应与生产尽可能接近（相同的 Python 版本、相同的依赖版本、相同的外部服务版本），避免"我机器上能跑"问题；第三是 **可追溯性**——每次 CI 运行都应产出测试报告、覆盖率报告作为 artifact，方便事后回溯定位。以下以 GitHub Actions 为例。

### 5.1 基础 CI 工作流

```yaml
# .github/workflows/tests.yml
name: Tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
          cache: "pip"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -e ".[dev,test]"

      - name: Run tests with coverage
        run: |
          pytest --cov=myapp --cov-report=xml --cov-report=term-missing \
                 --junitxml=junit.xml -n auto

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v4
        with:
          file: ./coverage.xml
          flags: unittests
          token: ${{ secrets.CODECOV_TOKEN }}

      - name: Upload test results
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: test-results-${{ matrix.python-version }}
          path: junit.xml
```

### 5.2 多环境矩阵 + 服务依赖

对于依赖 Postgres、Redis 的项目，用 service containers：

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_PASSWORD: test
          POSTGRES_DB: testdb
        ports:
          - 5432:5432
        options: >-
          --health-cmd pg_isready
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5

    steps:
      - uses: actions/checkout@v4
      # ... 安装依赖 ...
      - name: Run tests
        run: pytest -m "not slow" --cov=myapp
        env:
          DATABASE_URL: postgresql://postgres:test@localhost:5432/testdb
```

### 5.3 分层执行策略

将测试按标记分类，在 CI 中分层执行以加速反馈：

```python
# pyproject.toml
[tool.pytest.ini_options]
markers = [
    "slow: 耗时测试（>1s/条），仅在完整流水线中运行",
    "integration: 需要外部服务",
    "unit: 纯逻辑，无 I/O",
]
```

```yaml
# CI 中分阶段运行
- name: Fast unit tests (blocking)
  run: pytest -m "unit" --cov=myapp --cov-report=xml

- name: Integration tests (blocking, fewer cases)
  run: pytest -m "integration" --cov=myapp --cov-append --cov-report=xml

- name: Slow tests (optional, non-blocking)
  if: github.event_name == 'push' && github.ref == 'refs/heads/main'
  run: pytest -m "slow" --cov=myapp --cov-append
```

### 5.4 与 pre-commit 配合

CI 是最后防线，pre-commit 则把反馈提前到 `git commit` 阶段，减少 CI 浪费。

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: pytest-fast
        name: pytest (unit only)
        entry: pytest -m "unit" -x --tb=short
        language: system
        types: [python]
        pass_filenames: false
        always_run: true
```

---

## 附录：一站式配置参考

```toml
# pyproject.toml 完整测试相关片段
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
addopts = [
    "-ra",
    "--strict-markers",
    "--tb=short",
    "--cov=myapp",
    "--cov-report=term-missing:skip-covered",
    "--cov-branch",
]

[tool.coverage.run]
branch = true
source = ["myapp"]
omit = ["*/tests/*", "*/migrations/*"]

[tool.coverage.report]
fail_under = 80
show_missing = true
```

以上五节覆盖了从编写（fixture、mock、参数化）到度量（覆盖率）再到自动化保障（CI）的完整单元测试工作流。关键在于：**用 fixture 管理状态、在进程边界处 mock、用参数化放大覆盖、视覆盖率为探测器而非目标、将一切集成到 CI 形成纪律**。
