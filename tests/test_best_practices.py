"""
遵循 python-testing-patterns 最佳实践的测试示例

测试命名规范：test_<unit>_<scenario>_<expected_outcome>
"""

import pytest
from unittest.mock import Mock, patch, AsyncMock
from dataclasses import dataclass

# ═══════════════════════════════════════════════════════════════
# 被测试的代码
# ═══════════════════════════════════════════════════════════════


@dataclass
class User:
    """用户模型"""

    id: int
    name: str
    email: str
    is_active: bool = True


class UserService:
    """用户服务"""

    def __init__(self, repository):
        self.repository = repository

    def get_user(self, user_id: int) -> User | None:
        """获取用户"""
        return self.repository.find_by_id(user_id)

    def create_user(self, name: str, email: str) -> User:
        """创建用户"""
        if not name or not email:
            raise ValueError("Name and email are required")
        if self.repository.find_by_email(email):
            raise ValueError("Email already exists")
        return self.repository.create(name=name, email=email)

    def deactivate_user(self, user_id: int) -> bool:
        """停用用户"""
        user = self.repository.find_by_id(user_id)
        if not user:
            return False
        user.is_active = False
        self.repository.update(user)
        return True


class RetryClient:
    """带重试的客户端"""

    def __init__(self, client, max_retries=3):
        self.client = client
        self.max_retries = max_retries

    def fetch(self):
        """获取数据（带重试）"""
        last_error = None
        for _ in range(self.max_retries):
            try:
                return self.client.request()
            except ConnectionError as e:
                last_error = e
                continue
        raise last_error


# ═══════════════════════════════════════════════════════════════
# Fixture（共享测试数据）
# ═══════════════════════════════════════════════════════════════


@pytest.fixture
def mock_repository():
    """创建 Mock 仓库"""
    return Mock()


@pytest.fixture
def user_service(mock_repository):
    """创建用户服务"""
    return UserService(mock_repository)


@pytest.fixture
def sample_user():
    """创建示例用户"""
    return User(id=1, name="Alice", email="alice@example.com")


@pytest.fixture
def retry_client():
    """创建带重试的客户端"""
    client = Mock()
    return RetryClient(client, max_retries=3)


# ═══════════════════════════════════════════════════════════════
# 测试：AAA 模式（Arrange-Act-Assert）
# ═══════════════════════════════════════════════════════════════


class TestUserService:
    """用户服务测试"""

    def test_get_user_with_existing_id_returns_user(
        self, user_service, mock_repository, sample_user
    ):
        """测试：获取存在的用户返回用户对象"""
        # Arrange
        mock_repository.find_by_id.return_value = sample_user

        # Act
        result = user_service.get_user(1)

        # Assert
        assert result == sample_user
        mock_repository.find_by_id.assert_called_once_with(1)

    def test_get_user_with_nonexistent_id_returns_none(
        self, user_service, mock_repository
    ):
        """测试：获取不存在的用户返回 None"""
        # Arrange
        mock_repository.find_by_id.return_value = None

        # Act
        result = user_service.get_user(999)

        # Assert
        assert result is None

    def test_create_user_with_valid_data_returns_user(
        self, user_service, mock_repository
    ):
        """测试：使用有效数据创建用户返回用户对象"""
        # Arrange
        mock_repository.find_by_email.return_value = None
        mock_repository.create.return_value = User(
            id=1, name="Bob", email="bob@example.com"
        )

        # Act
        result = user_service.create_user("Bob", "bob@example.com")

        # Assert
        assert result.name == "Bob"
        assert result.email == "bob@example.com"
        mock_repository.create.assert_called_once()

    def test_create_user_with_empty_name_raises_value_error(self, user_service):
        """测试：空名称创建用户抛出 ValueError"""
        # Arrange & Act & Assert
        with pytest.raises(ValueError, match="Name and email are required"):
            user_service.create_user("", "test@example.com")

    def test_create_user_with_duplicate_email_raises_value_error(
        self, user_service, mock_repository, sample_user
    ):
        """测试：重复邮箱创建用户抛出 ValueError"""
        # Arrange
        mock_repository.find_by_email.return_value = sample_user

        # Act & Assert
        with pytest.raises(ValueError, match="Email already exists"):
            user_service.create_user("Test", "alice@example.com")

    def test_deactivate_user_with_existing_user_returns_true(
        self, user_service, mock_repository, sample_user
    ):
        """测试：停用存在的用户返回 True"""
        # Arrange
        mock_repository.find_by_id.return_value = sample_user

        # Act
        result = user_service.deactivate_user(1)

        # Assert
        assert result is True
        assert sample_user.is_active is False
        mock_repository.update.assert_called_once_with(sample_user)

    def test_deactivate_user_with_nonexistent_user_returns_false(
        self, user_service, mock_repository
    ):
        """测试：停用不存在的用户返回 False"""
        # Arrange
        mock_repository.find_by_id.return_value = None

        # Act
        result = user_service.deactivate_user(999)

        # Assert
        assert result is False


# ═══════════════════════════════════════════════════════════════
# 测试：重试行为
# ═══════════════════════════════════════════════════════════════


class TestRetryBehavior:
    """重试行为测试"""

    def test_retries_on_transient_error(self, retry_client):
        """测试：瞬态错误时重试"""
        # Arrange
        retry_client.client.request.side_effect = [
            ConnectionError("Failed"),
            ConnectionError("Failed"),
            {"status": "ok"},
        ]

        # Act
        result = retry_client.fetch()

        # Assert
        assert result == {"status": "ok"}
        assert retry_client.client.request.call_count == 3

    def test_gives_up_after_max_retries(self, retry_client):
        """测试：超过最大重试次数后放弃"""
        # Arrange
        retry_client.client.request.side_effect = ConnectionError("Failed")

        # Act & Assert
        with pytest.raises(ConnectionError):
            retry_client.fetch()
        assert retry_client.client.request.call_count == 3

    def test_does_not_retry_on_success(self, retry_client):
        """测试：成功时不重试"""
        # Arrange
        retry_client.client.request.return_value = {"status": "ok"}

        # Act
        result = retry_client.fetch()

        # Assert
        assert result == {"status": "ok"}
        assert retry_client.client.request.call_count == 1


# ═══════════════════════════════════════════════════════════════
# 测试：参数化
# ═══════════════════════════════════════════════════════════════


class TestParameterized:
    """参数化测试"""

    @pytest.mark.parametrize(
        "name,email,should_raise",
        [
            ("Alice", "alice@example.com", False),
            ("Bob", "bob@example.com", False),
            ("", "test@example.com", True),
            ("Test", "", True),
            ("", "", True),
        ],
    )
    def test_create_user_validation(
        self, user_service, mock_repository, name, email, should_raise
    ):
        """测试：用户创建验证"""
        # Arrange
        mock_repository.find_by_email.return_value = None

        # Act & Assert
        if should_raise:
            with pytest.raises(ValueError):
                user_service.create_user(name, email)
        else:
            mock_repository.create.return_value = User(id=1, name=name, email=email)
            result = user_service.create_user(name, email)
            assert result.name == name


# ═══════════════════════════════════════════════════════════════
# 测试：Mock 边界
# ═══════════════════════════════════════════════════════════════


class TestMockBoundaries:
    """Mock 边界测试"""

    def test_mock_context_manager(self):
        """测试：Mock 上下文管理器"""
        with patch("tests.test_best_practices.UserService") as MockService:
            mock_instance = MockService.return_value
            mock_instance.get_user.return_value = User(
                id=1, name="Mock", email="mock@example.com"
            )

            service = MockService()
            user = service.get_user(1)

            assert user.name == "Mock"

    def test_mock_side_effect_for_exceptions(self):
        """测试：Mock 异常副作用"""
        mock_repo = Mock()
        mock_repo.find_by_id.side_effect = ConnectionError("DB unavailable")

        service = UserService(mock_repo)

        with pytest.raises(ConnectionError):
            service.get_user(1)

    @pytest.mark.asyncio
    async def test_async_mock(self):
        """测试：异步 Mock"""
        mock_func = AsyncMock(return_value="async result")
        result = await mock_func()
        assert result == "async result"


# ═══════════════════════════════════════════════════════════════
# 测试：Fixture 作用域
# ═══════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def database_connection():
    """模块级 Fixture：数据库连接"""
    print("\n[Module Setup] Creating database connection")
    conn = {"connected": True}
    yield conn
    print("\n[Module Teardown] Closing database connection")


@pytest.fixture(scope="function")
def transaction():
    """函数级 Fixture：事务"""
    print("\n[Function Setup] Starting transaction")
    tx = {"active": True}
    yield tx
    print("\n[Function Setup] Rolling back transaction")


class TestFixtureScopes:
    """Fixture 作用域测试"""

    def test_with_database(self, database_connection):
        """使用数据库连接 Fixture"""
        assert database_connection["connected"] is True

    def test_with_transaction(self, transaction):
        """使用事务 Fixture"""
        assert transaction["active"] is True


# ═══════════════════════════════════════════════════════════════
# 测试：标记
# ═══════════════════════════════════════════════════════════════


class TestMarkers:
    """测试标记示例"""

    @pytest.mark.slow
    def test_slow_operation(self):
        """标记为慢测试"""
        import time

        time.sleep(0.1)
        assert True

    @pytest.mark.skip(reason="功能未实现")
    def test_future_feature(self):
        """跳过未实现的功能"""
        pass

    @pytest.mark.xfail(reason="已知问题")
    def test_known_issue(self):
        """预期失败"""
        assert False
