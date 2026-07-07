# Python 单元测试设计指南

---

## 一、Fixture 设计

Fixture 是测试的「前置准备环境」——它为每条用例提供干净、可复用的初始状态。在 Python 中，Fixture 主要通过 `pytest.fixture` 装饰器实现，其设计的核心原则是 **「单一职责 + 按需组合」**：一个 fixture 只做一件事，通过依赖注入灵活拼装。

**作用域选择**是 fixture 设计的第一个关键决策。`pytest.fixture(scope="function")` 为每个测试函数重建 fixture（默认，隔离性最强）；`scope="class"` 让同一测试类共享一份实例；`scope="module"` 在模块内复用；`scope="session"` 则是全局单例。选择时需权衡隔离性与执行速度——例如数据库连接适合 session 级别，而临时数据构造则应保持 function 级别以免用例间相互污染。

**工厂 Fixture** 是另一个重要模式：与其在 fixture 中返回固定对象，不如返回一个工厂函数，让测试用例自行决定参数。这在需要「同一类对象的不同变体」时尤其有用。此外，**fixture 可以依赖 fixture** —— pytest 会按拓扑序解析依赖图，自动注入。比如 `db_cursor` 依赖 `db_connection`，pytest 保证先建立连接再创建游标，拆卸时反向执行 teardown。

`yield` 语义让 setup/teardown 写在同一函数体内：`yield` 之前是 setup，之后是 teardown。这比 `setUp/tearDown` 更直观，也更容易做资源管理。对于需要确保清理的场景（如临时文件删除），推荐在 `yield` 后用 `try...finally` 或在 `yield` 后直接放清理代码。

`conftest.py` 是 fixture 的「发布中心」。放在包目录下的 `conftest.py` 中定义的 fixture 会被该包及子包的所有测试自动发现，无需显式 import。这实现了 fixture 的分层复用：项目根 `conftest.py` 放全局 fixture（如数据库 URL），子包 `conftest.py` 放领域专用 fixture。

```python
# conftest.py - 项目根
import pytest
from myapp.db import create_connection, create_tables, drop_tables

@pytest.fixture(scope="session")
def db_url():
    """全局数据库 URL，整个测试会话不变"""
    return "postgresql://test:test@localhost:5432/test_db"

@pytest.fixture(scope="function")
def db_session(db_url):
    """每个测试函数获得独立事务，commit 后在 teardown 回滚"""
    conn = create_connection(db_url)
    create_tables(conn)
    # yield 前 = setup
    transaction = conn.begin()
    yield conn
    # yield 后 = teardown（即使用例失败也会执行）
    transaction.rollback()
    drop_tables(conn)
    conn.close()

@pytest.fixture
def user_factory(db_session):
    """工厂 fixture：让测试自行决定创建怎样的用户"""
    created_ids = []

    def _create(name: str, email: str, role: str = "user"):
        cursor = db_session.cursor()
        cursor.execute(
            "INSERT INTO users (name, email, role) VALUES (?, ?, ?) RETURNING id",
            (name, email, role),
        )
        user_id = cursor.fetchone()[0]
        created_ids.append(user_id)
        return user_id

    yield _create
    # 清理所有通过工厂创建的用户
    for uid in created_ids:
        db_session.cursor().execute("DELETE FROM users WHERE id = ?", (uid,))


# test_user_service.py
class TestUserService:
    def test_admin_can_delete(self, db_session, user_factory):
        admin_id = user_factory("admin", "a@x.com", role="admin")
        user_id = user_factory("user", "u@x.com")
        svc = UserService(db_session)
        svc.delete_user(admin_id, user_id)
        assert svc.get_user(user_id) is None

    def test_regular_user_cannot_delete(self, db_session, user_factory):
        u1 = user_factory("alice", "alice@x.com", role="user")
        u2 = user_factory("bob", "bob@x.com")
        svc = UserService(db_session)
        with pytest.raises(PermissionError):
            svc.delete_user(u1, u2)
```

> **本节字数**：约 650 字

---

## 二、Mock 策略

Mock（模拟）是单元测试中隔离外部依赖的核心手段。Python 生态中 `unittest.mock` 提供 `Mock`、`MagicMock`、`patch` 三大基石，而策略的关键不在「会不会用」，在于「何时用、用到什么程度」。

**第一原则：优先真实对象，Mock 只用于边界。** 对纯函数、值对象、领域实体，永远用真实实例——它们不涉及 I/O，运行快且无副作用。Mock 应精确锁定在「系统边界」上：HTTP 调用、数据库查询、文件系统、时间/随机数、第三方 SDK。如果你发现自己在 mock 自己写的类的内部方法，说明测试对象职责太多，该重构了。

**patch 的两种用法：装饰器 vs 上下文管理器。** `@patch("module.attribute")` 作用于整个测试函数，适合该测试中所有调用都应被 mock 的场景；`with patch(...)` 则适合只在某段代码中 mock。注意 `patch` 的字符串路径是「被测试代码中 import 的位置」，不是定义位置——这是最常见的踩坑点。

**Mock 返回值与副作用。** `mock.return_value` 设定每次调用的返回值；`mock.side_effect` 可以依次返回多个值、抛异常，或执行一个函数。用 `side_effect` 让 mock 根据输入动态响应的做法很常见。此外，`spec` 参数能约束 mock 只允许访问真实类的属性——`Mock(spec=RealClass)` 会在你访问不存在的属性时直接报错，避免「mock 太宽松导致假阳性」。

**断言调用行为** 同样重要：`mock.assert_called_once_with(args)`、`mock.assert_not_called()` 等能验证被测代码是否正确与外部协作。但不要过度断言——只验证与测试目标直接相关的调用，避免对 mock 的调用顺序、精确参数做无意义的紧耦合验证。

```python
# weather_service.py
import requests
from datetime import date


def fetch_forecast(city: str) -> dict:
    resp = requests.get(
        f"https://api.weather.com/v1/forecast",
        params={"city": city, "date": str(date.today())},
        timeout=5,
    )
    resp.raise_for_status()
    data = resp.json()
    return {"temp": data["main"]["temp"], "humidity": data["main"]["humidity"]}


# test_weather_service.py
from unittest.mock import Mock, patch, sentinel
import pytest
import requests
from weather_service import fetch_forecast


class TestFetchForecast:
    @patch("weather_service.requests.get")
    def test_returns_parsed_weather(self, mock_get):
        # arrange：构造 mock 响应
        mock_response = Mock(spec=requests.Response)
        mock_response.json.return_value = {
            "main": {"temp": 22.5, "humidity": 65}
        }
        mock_response.raise_for_status.return_value = None
        mock_get.return_value = mock_response

        # act
        result = fetch_forecast("Beijing")

        # assert 返回值
        assert result == {"temp": 22.5, "humidity": 65}
        # assert 与外部协作者的交互
        mock_get.assert_called_once()
        call_args = mock_get.call_args
        assert call_args[1]["params"]["city"] == "Beijing"

    @patch("weather_service.requests.get")
    def test_raises_on_http_error(self, mock_get):
        mock_get.side_effect = requests.HTTPError("503 Server Error")
        with pytest.raises(requests.HTTPError):
            fetch_forecast("Atlantis")

    def test_uses_side_effect_for_dynamic_response(self):
        """演示 side_effect 用函数实现动态返回值"""
        mock_service = Mock()

        def dynamic_lookup(user_id):
            return {"id": user_id, "name": f"user_{user_id}"}

        mock_service.get_user.side_effect = dynamic_lookup

        assert mock_service.get_user(1)["name"] == "user_1"
        assert mock_service.get_user(2)["name"] == "user_2"
        mock_service.get_user.assert_any_call(1)
```

> **本节字数**：约 580 字

---

## 三、参数化

参数化是「同一测试逻辑 × 多组输入」的声明式写法，避免 copy-paste 驱动的测试膨胀。pytest 提供 `@pytest.mark.parametrize` 装饰器，其核心思想是 **正交分解**：把测试逻辑、输入数据、期望结果三者分离，让意图一目了然。

**基础用法**：`@pytest.mark.parametrize("arg1,arg2", [(v1,v2), (v3,v4)])`。第一个参数是逗号分隔的参数名，第二个是值列表。pytest 会为每组值生成独立测试用例，失败时精准指出哪一组出了问题。**命名规范**：参数名应直接映射到被测试参数名，避免间接映射增加认知负担。

**多层参数化（笛卡尔积）**：连续叠加多个 `parametrize` 装饰器，pytest 会自动求所有组合的笛卡尔积。这在测试「多个正交维度」时非常高效——比如 `(role) × (action) × (resource)` 可在几行内覆盖数十个场景。但要注意组合爆炸，合理控制维度数。

**`indirect` 参数**：当 `parametrize` 的值应传给 **同名的 fixture** 而不是直接当函数参数时，使用 `indirect=True`。这让你能用 fixture 的 setup/teardown 能力为每组参数准备环境（如不同的数据库方言、不同的配置文件路径）。

**`pytest.param`** 提供细粒度控制：可以单独为一组参数设置 `marks`（如 `pytest.mark.xfail`）、`id`（自定义测试用例名称）。这在预期某组输入会失败或需要跳过的场景中不可或缺。

**参数化 + fixture 组合**：当一个 fixture 的返回值有多个变体时，`params` 参数让 fixture 自身变成参数化的——pytest 会为该 fixture 的每个 `params` 值重新运行依赖它的所有测试。这比在每个测试上手动 parametrize 更 DRY。

```python
import pytest
from dataclasses import dataclass


@dataclass
class PaymentResult:
    success: bool
    message: str


def process_payment(amount: int, balance: int, method: str) -> PaymentResult:
    if method not in ("card", "wallet"):
        raise ValueError(f"Unknown method: {method}")
    if amount <= 0:
        raise ValueError("Amount must be positive")
    if balance >= amount:
        return PaymentResult(True, "ok")
    return PaymentResult(False, "insufficient funds")


class TestProcessPayment:
    # 单层参数化：成功 / 失败路径
    @pytest.mark.parametrize(
        "amount,balance,expected_success",
        [
            pytest.param(100, 200, True, id="enough_balance"),
            pytest.param(100, 100, True, id="exact_balance"),
            pytest.param(200, 100, False, id="insufficient"),
            pytest.param(1, 0, False, id="zero_balance"),
        ],
    )
    def test_payment_outcome(self, amount, balance, expected_success):
        result = process_payment(amount, balance, "card")
        assert result.success == expected_success

    # 多层参数化：method × (amount, balance)
    @pytest.mark.parametrize("method", ["card", "wallet"])
    @pytest.mark.parametrize(
        "amount,balance",
        [(50, 100), (100, 50)],
    )
    def test_all_methods(self, amount, balance, method):
        result = process_payment(amount, balance, method)
        assert isinstance(result, PaymentResult)  # 不抛异常即通过

    # 错误路径参数化
    @pytest.mark.parametrize(
        "amount,balance,method,expected_exc",
        [
            (-5, 100, "card", ValueError),
            (0, 100, "card", ValueError),
            (100, 1000, "bitcoin", ValueError),
        ],
    )
    def test_invalid_inputs(self, amount, balance, method, expected_exc):
        with pytest.raises(expected_exc):
            process_payment(amount, balance, method)


# conftest.py —— 参数化 fixture，用 params 一次定义多环境
@pytest.fixture(
    params=[
        "sqlite:///:memory:",
        pytest.param("postgresql://localhost/test", marks=pytest.mark.slow),
    ],
    ids=["sqlite", "postgres"],
)
def db_backend(request):
    """同一个 fixture，两种数据库后端，所有依赖它的测试自动跑两遍"""
    engine = create_engine(request.param)
    yield engine
    engine.dispose()
```

> **本节字数**：约 620 字

---

## 四、覆盖率工具

代码覆盖率衡量「测试执行了哪些代码」，是测试质量的必要非充分指标。Python 生态的标杆工具是 **`coverage.py`**，配合 pytest 的 `pytest-cov` 插件实现零摩擦集成。覆盖率不能保证测试正确，但它能暴露「从未执行过的代码路径」——这是肉眼 code review 很难发现的死角。

**覆盖率类型**：`coverage.py` 支持多种度量维度。**行覆盖率（line coverage）** 最常用，统计哪些行被执行过；**分支覆盖率（branch coverage）** 检查 `if`/`else`、`try`/`except` 等分支是否都走到——行覆盖率 100% 不代表分支覆盖率 100%，下面的代码示例会清楚展示这一点；**路径覆盖率** 理论完备但组合爆炸，实践中较少使用。建议至少同时启用行覆盖和分支覆盖：`--cov=myapp --cov-branch`。

**配置与报告**：通过 `.coveragerc` 或 `pyproject.toml` 配置排除规则（忽略测试文件自身、`__init__.py` 中的 import 语句、抽象基类中的 `raise NotImplementedError` 等），避免覆盖率被噪声拉低。报告格式方面：`term`（终端摘要）适合本地快速查看，`html`（生成 `htmlcov/index.html`）适合详细审查每一行的覆盖状态，`xml`（Cobertura 格式）是 CI 集成的事实标准。

**关键心态：覆盖率是「发现盲区」的工具，不是「追求数字」的游戏。** 与其追逐 100%，不如定期审视未覆盖行：如果某行代码确实不可能在测试中走到，加 `# pragma: no cover` 明确标注；如果某行「理论上可达但测试没覆盖」，说明要么测试不足，要么代码是死代码应删除。100% 行覆盖很容易通过「不写断言」的测试达成，但毫无意义。

```python
# 安装依赖
# pip install pytest coverage pytest-cov

# 基础用法
# pytest --cov=myapp --cov-report=term --cov-report=html


# -------------- 示例：分支覆盖 vs 行覆盖 --------------
# business.py
def classify_temperature(celsius: float) -> str:
    if celsius is None:             # 分支 1: is None / not None
        return "unknown"
    if celsius < 0:                 # 分支 2: <0 / >=0
        return "freezing"
    elif celsius < 15:              # 分支 3: <15 / >=15
        return "cold"
    elif celsius < 30:              # 分支 4: <30 / >=30
        return "warm"
    else:
        return "hot"


# test_business.py
class TestClassify:
    def test_line_100_but_branch_miss(self):
        """这条用例走遍所有行，但只覆盖了 60% 的分支"""
        assert classify_temperature(20) == "warm"
        # 漏了: None / <0 / <15 / >=30 —— 行覆盖 100%，分支覆盖 ~60%


# pyproject.toml 中 coverage 配置
# [tool.coverage.run]
# source = ["myapp"]
# branch = true
# omit = ["*/tests/*", "*/migrations/*"]
#
# [tool.coverage.report]
# exclude_also = [
#     "raise NotImplementedError",
#     "if TYPE_CHECKING:",
#     "pragma: no cover",
# ]
```

> **本节字数**：约 520 字

---

## 五、CI 集成

将单元测试嵌入持续集成（CI）管道，才能让测试从「偶尔跑一下」变成「每次变更的守门人」。以 GitHub Actions 为例，CI 集成的核心诉求是：**快速失败（fail fast）、清晰报告、覆盖率门禁**。

**矩阵策略**：用 CI 的 matrix 功能同时测试多个 Python 版本和依赖组合。`strategy.matrix` 让同一套测试在 Python 3.10 / 3.11 / 3.12 上并行执行，尽早发现版本兼容问题。还可以加一维 `dependencies` 来验证「最小依赖」和「最新依赖」都能通过。

**缓存与加速**：用 `actions/cache` 缓存 `pip` 包（key 基于 `pyproject.toml` / `requirements.txt` 的 hash），避免每次从头安装。`pytest` 加上 `-n auto`（`pytest-xdist`）并行执行，大项目可提速 3-5 倍。此外，用 `--exitfirst`（首次失败即终止）或 `--maxfail=5` 限制失败用例数，减少 CI 资源浪费。

**覆盖率门禁**：在 CI 中设置最低覆盖率阈值——`--cov-fail-under=80` 让 `pytest` 在覆盖率不足时返回非零退出码，直接让 pipeline 变红。这个数字不应一开始就设太高；建议从当前实际覆盖率 +5% 起步，逐步提升。

**报告发布与可追溯性**：将覆盖率报告上传为 CI artifact（`actions/upload-artifact`），或在 PR 评论中自动贴出覆盖率变化（`pytest-cov` 输出 + GitHub Actions 的 `GITHUB_STEP_SUMMARY`）。这样审查者不需要拉代码就能看到测试影响。对于使用 GitHub PR 的团队，结合 `pytest--github-actions-annotate` 插件可以在 PR diff 上直接标注失败行。

```yaml
# .github/workflows/test.yml
name: Test Suite

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false          # 某个版本失败不影响其他版本继续跑
      matrix:
        python-version: ["3.10", "3.11", "3.12"]

    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Cache pip packages
        uses: actions/cache@v4
        with:
          path: ~/.cache/pip
          key: pip-${{ runner.os }}-${{ matrix.python-version }}-${{ hashFiles('pyproject.toml') }}
          restore-keys: |
            pip-${{ runner.os }}-${{ matrix.python-version }}-

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Run tests with coverage
        run: |
          pytest -n auto \
            --cov=myapp \
            --cov-branch \
            --cov-fail-under=80 \
            --cov-report=xml:coverage.xml \
            --cov-report=term \
            --junitxml=junit.xml \
            tests/

      - name: Upload coverage to artifact
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: coverage-${{ matrix.python-version }}
          path: |
            coverage.xml
            junit.xml

      - name: Post coverage summary
        if: always()
        run: |
          echo "### Coverage (Python ${{ matrix.python-version }})" >> $GITHUB_STEP_SUMMARY
          python -m coverage report --format=markdown >> $GITHUB_STEP_SUMMARY
```

> **本节字数**：约 500 字

---

## 附录：命令速查

| 场景 | 命令 |
|------|------|
| 运行全部测试 + 覆盖率 | `pytest --cov=myapp --cov-report=html` |
| 仅运行失败的用例 | `pytest --lf` |
| 并行执行 | `pytest -n auto` |
| 首次失败即停止 | `pytest -x` |
| 覆盖率不达标即失败 | `pytest --cov=myapp --cov-fail-under=80` |
| 生成 JUnit XML | `pytest --junitxml=report.xml` |
| 只看最慢的 10 条用例 | `pytest --durations=10` |

---

*本指南涵盖 Python 单元测试设计的五个核心维度：fixture 构建、mock 策略选择、参数化写法、覆盖率工具运用、CI 流水线集成。各节既独立成章又互为支撑——良好的 fixture 设计减少了 mock 的需求，合理的参数化让覆盖率更易提升，而 CI 集成则为这一切提供自动化保障。*
