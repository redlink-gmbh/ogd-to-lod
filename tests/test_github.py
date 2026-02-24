"""Tests for GitHub integration service."""

from unittest.mock import MagicMock, patch

import pytest
from github import Auth, GithubException

from ogd_to_lod.config import GitHubConfig
from ogd_to_lod.github import GitHubService, GitHubError, PRCreationError


@pytest.fixture
def github_config():
    """Create a GitHub configuration for testing."""
    return GitHubConfig(
        repo="test-org/test-repo",
        token="test-token",
    )


@pytest.fixture
def mock_github():
    """Create a mock GitHub client."""
    with patch("ogd_to_lod.github.service.Github") as mock:
        yield mock


class TestGitHubService:
    """Tests for GitHubService class."""

    def test_init(self, github_config, mock_github):
        """Test service initialization."""
        service = GitHubService(github_config)

        mock_github.assert_called_once()
        auth_arg = mock_github.call_args.kwargs["auth"]
        assert isinstance(auth_arg, Auth.Token)
        assert auth_arg.token == "test-token"
        assert service._config == github_config

    def test_repo_lazy_loading(self, github_config, mock_github):
        """Test that repository is lazily loaded."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        service = GitHubService(github_config)

        # Should not have loaded repo yet
        mock_github.return_value.get_repo.assert_not_called()

        # Access repo property
        repo = service.repo

        # Should have loaded repo now
        mock_github.return_value.get_repo.assert_called_once_with("test-org/test-repo")
        assert repo == mock_repo

    def test_repo_access_error(self, github_config, mock_github):
        """Test error handling when repository access fails."""
        mock_github.return_value.get_repo.side_effect = GithubException(
            status=404,
            data={"message": "Not Found"},
            headers={},
        )

        service = GitHubService(github_config)

        with pytest.raises(GitHubError) as exc_info:
            _ = service.repo

        assert "Failed to access repository" in str(exc_info.value)

    def test_sanitize_name(self, github_config, mock_github):
        """Test name sanitization for branches and files."""
        service = GitHubService(github_config)

        # Basic sanitization
        assert service._sanitize_name("test name") == "test-name"
        assert service._sanitize_name("test_name") == "test-name"
        assert service._sanitize_name("Test-Name") == "test-name"

        # Special characters
        assert service._sanitize_name("test@name#123") == "testname123"
        assert service._sanitize_name("test--name") == "test-name"

        # Edge cases
        assert service._sanitize_name("---") == "mapping"
        assert service._sanitize_name("") == "mapping"
        assert service._sanitize_name("-test-") == "test"

    def test_create_mapping_pr_success(self, github_config, mock_github):
        """Test successful PR creation."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        # Mock branch operations
        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # Mock file doesn't exist
        mock_repo.get_contents.side_effect = GithubException(
            status=404,
            data={"message": "Not Found"},
            headers={},
        )

        # Mock PR creation
        mock_pr = MagicMock()
        mock_pr.number = 42
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/42"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        result = service.create_mapping_pr(
            mapping_name="test-mapping",
            rml_content="@prefix rr: <...> .",
            description="Test description",
        )

        assert result.pr_number == 42
        assert result.pr_url == "https://github.com/test-org/test-repo/pull/42"
        assert result.branch_name == "mapping/test-mapping"

        # Verify branch was created
        mock_repo.create_git_ref.assert_called_once_with(
            ref="refs/heads/mapping/test-mapping",
            sha="abc123",
        )

        # Verify file was created
        mock_repo.create_file.assert_called_once()
        call_kwargs = mock_repo.create_file.call_args[1]
        assert call_kwargs["path"] == "mappings/test-mapping/mapping.yarrrml.yaml"
        assert call_kwargs["content"] == "@prefix rr: <...> ."
        assert call_kwargs["branch"] == "mapping/test-mapping"

        # Verify PR was created
        mock_repo.create_pull.assert_called_once()
        pr_kwargs = mock_repo.create_pull.call_args[1]
        assert pr_kwargs["title"] == "Add mapping: test-mapping"
        assert pr_kwargs["body"] == "Test description"
        assert pr_kwargs["head"] == "mapping/test-mapping"
        assert pr_kwargs["base"] == "main"

    def test_create_mapping_pr_branch_exists(self, github_config, mock_github):
        """Test PR creation when branch already exists (422 from create_git_ref)."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # create_git_ref fails with 422 (branch already exists)
        mock_repo.create_git_ref.side_effect = GithubException(
            status=422,
            data={"message": "Reference already exists"},
            headers={},
        )

        # get_git_ref returns the existing ref for the fallback edit
        mock_existing_ref = MagicMock()
        mock_repo.get_git_ref.return_value = mock_existing_ref

        # Mock file doesn't exist
        mock_repo.get_contents.side_effect = GithubException(
            status=404,
            data={"message": "Not Found"},
            headers={},
        )

        # Mock PR creation
        mock_pr = MagicMock()
        mock_pr.number = 43
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/43"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        result = service.create_mapping_pr(
            mapping_name="existing-mapping",
            rml_content="@prefix rr: <...> .",
            description="Test description",
        )

        # Should have tried to create first, then fallen back to edit
        mock_repo.create_git_ref.assert_called_once()
        mock_existing_ref.edit.assert_called_once_with(sha="abc123", force=True)

        assert result.pr_number == 43

    def test_create_mapping_pr_pr_already_exists(self, github_config, mock_github):
        """Test that an existing open PR is updated instead of failing."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo
        mock_repo.owner.login = "test-org"

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # Mock file doesn't exist
        mock_repo.get_contents.side_effect = GithubException(
            status=404,
            data={"message": "Not Found"},
            headers={},
        )

        # create_pull fails with 422 (PR already exists)
        mock_repo.create_pull.side_effect = GithubException(
            status=422,
            data={"message": "A pull request already exists"},
            headers={},
        )

        # Mock finding the existing PR
        mock_existing_pr = MagicMock()
        mock_existing_pr.number = 99
        mock_existing_pr.html_url = "https://github.com/test-org/test-repo/pull/99"
        mock_repo.get_pulls.return_value = [mock_existing_pr]

        service = GitHubService(github_config)
        result = service.create_mapping_pr(
            mapping_name="rerun-mapping",
            rml_content="@prefix rr: <...> .",
            description="Updated description",
        )

        # Should have updated the existing PR
        mock_existing_pr.edit.assert_called_once_with(
            title="Add mapping: rerun-mapping",
            body="Updated description",
        )
        assert result.pr_number == 99
        assert result.pr_url == "https://github.com/test-org/test-repo/pull/99"

    def test_create_mapping_pr_file_exists(self, github_config, mock_github):
        """Test PR creation when file already exists in branch."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # Mock existing file
        mock_existing_file = MagicMock()
        mock_existing_file.sha = "file-sha-123"
        mock_repo.get_contents.return_value = mock_existing_file

        # Mock PR creation
        mock_pr = MagicMock()
        mock_pr.number = 44
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/44"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        result = service.create_mapping_pr(
            mapping_name="update-mapping",
            rml_content="@prefix rr: <...> .",
            description="Updated mapping",
        )

        # Should have updated the file
        mock_repo.update_file.assert_called_once()
        update_kwargs = mock_repo.update_file.call_args[1]
        assert update_kwargs["sha"] == "file-sha-123"

        # Should NOT have created a new file
        mock_repo.create_file.assert_not_called()

        assert result.pr_number == 44

    def test_create_mapping_pr_api_error(self, github_config, mock_github):
        """Test error handling for API errors during PR creation."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        # Mock API error
        mock_repo.get_branch.side_effect = GithubException(
            status=500,
            data={"message": "Internal Server Error"},
            headers={},
        )

        service = GitHubService(github_config)

        with pytest.raises(PRCreationError) as exc_info:
            service.create_mapping_pr(
                mapping_name="error-mapping",
                rml_content="@prefix rr: <...> .",
                description="Test description",
            )

        assert "Failed to create PR" in str(exc_info.value)

    def test_create_mapping_pr_custom_base_branch(self, github_config, mock_github):
        """Test PR creation with custom base branch."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        # Mock file doesn't exist
        mock_repo.get_contents.side_effect = GithubException(
            status=404,
            data={"message": "Not Found"},
            headers={},
        )

        # Mock PR creation
        mock_pr = MagicMock()
        mock_pr.number = 45
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/45"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        result = service.create_mapping_pr(
            mapping_name="develop-mapping",
            rml_content="@prefix rr: <...> .",
            description="Test description",
            base_branch="develop",
        )

        # Verify correct base branch was used
        mock_repo.get_branch.assert_called_with("develop")

        pr_kwargs = mock_repo.create_pull.call_args[1]
        assert pr_kwargs["base"] == "develop"

        assert result.pr_number == 45

    def test_subfolder_layout(self, github_config, mock_github):
        """Test that file path uses subfolder layout: mappings/{name}/mapping.yarrrml.yaml."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        mock_repo.get_contents.side_effect = GithubException(
            status=404, data={"message": "Not Found"}, headers={},
        )

        mock_pr = MagicMock()
        mock_pr.number = 50
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/50"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        service.create_mapping_pr(
            mapping_name="my-dataset",
            rml_content="@prefix rr: <...> .",
            description="Test",
        )

        call_kwargs = mock_repo.create_file.call_args[1]
        assert call_kwargs["path"] == "mappings/my-dataset/mapping.yarrrml.yaml"

    def test_dcat_file_committed_alongside_rml(self, github_config, mock_github):
        """Test that DCAT file is committed when content and filename provided."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        mock_repo.get_contents.side_effect = GithubException(
            status=404, data={"message": "Not Found"}, headers={},
        )

        mock_pr = MagicMock()
        mock_pr.number = 51
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/51"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        service.create_mapping_pr(
            mapping_name="with-dcat",
            rml_content="@prefix rr: <...> .",
            description="Test with DCAT",
            dcat_content="@prefix dcat: <...> .",
            dcat_filename="metadata.ttl",
        )

        # Should have two create_file calls: RML + DCAT
        assert mock_repo.create_file.call_count == 2
        calls = mock_repo.create_file.call_args_list

        rml_path = calls[0][1]["path"]
        dcat_path = calls[1][1]["path"]
        assert rml_path == "mappings/with-dcat/mapping.yarrrml.yaml"
        assert dcat_path == "mappings/with-dcat/metadata.ttl"
        assert calls[1][1]["content"] == "@prefix dcat: <...> ."

    def test_no_dcat_file_when_not_provided(self, github_config, mock_github):
        """Test that only RML file is committed when no DCAT content provided."""
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        mock_branch = MagicMock()
        mock_branch.commit.sha = "abc123"
        mock_repo.get_branch.return_value = mock_branch

        mock_repo.get_contents.side_effect = GithubException(
            status=404, data={"message": "Not Found"}, headers={},
        )

        mock_pr = MagicMock()
        mock_pr.number = 52
        mock_pr.html_url = "https://github.com/test-org/test-repo/pull/52"
        mock_repo.create_pull.return_value = mock_pr

        service = GitHubService(github_config)
        service.create_mapping_pr(
            mapping_name="no-dcat",
            rml_content="@prefix rr: <...> .",
            description="Test without DCAT",
        )

        # Should have exactly one create_file call (RML only)
        assert mock_repo.create_file.call_count == 1
        call_kwargs = mock_repo.create_file.call_args[1]
        assert call_kwargs["path"] == "mappings/no-dcat/mapping.yarrrml.yaml"
