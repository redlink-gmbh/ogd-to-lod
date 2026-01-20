"""GitHub service for branch creation and PR management."""

from dataclasses import dataclass
from typing import Any

from github import Github, GithubException
from github.Repository import Repository

from ogd_to_lod.config import GitHubConfig
from ogd_to_lod.logging import get_logger

logger = get_logger(__name__)


class GitHubError(Exception):
    """Base exception for GitHub operations."""


class PRCreationError(GitHubError):
    """Exception raised when PR creation fails."""


@dataclass
class PRResult:
    """Result of a PR creation operation."""

    pr_number: int
    pr_url: str
    branch_name: str


class GitHubService:
    """Service for GitHub operations including branch and PR management.

    This service handles:
    - Creating branches for new mappings
    - Committing RML files to the repository
    - Creating pull requests with descriptions
    """

    MAPPINGS_FOLDER = "mappings"

    def __init__(self, config: GitHubConfig):
        """Initialize the GitHub service.

        Args:
            config: GitHub configuration with repo and token.
        """
        self._config = config
        self._client = Github(config.token)
        self._repo: Repository | None = None

    @property
    def repo(self) -> Repository:
        """Get the repository object, lazily loaded.

        Returns:
            Repository object for the configured repo.

        Raises:
            GitHubError: If repository cannot be accessed.
        """
        if self._repo is None:
            try:
                self._repo = self._client.get_repo(self._config.repo)
                logger.debug(f"Connected to repository: {self._config.repo}")
            except GithubException as e:
                logger.error(f"Failed to access repository: {e}")
                raise GitHubError(f"Failed to access repository '{self._config.repo}': {e}")
        return self._repo

    def create_mapping_pr(
        self,
        mapping_name: str,
        rml_content: str,
        description: str,
        base_branch: str = "main",
    ) -> PRResult:
        """Create a PR with a new RML mapping.

        Creates a new branch, commits the RML file, and opens a pull request.

        Args:
            mapping_name: Name for the mapping (used for branch and file names).
            rml_content: The RML Turtle content to commit.
            description: Human-readable description for the PR body.
            base_branch: Branch to create PR against (default: main).

        Returns:
            PRResult with PR number, URL, and branch name.

        Raises:
            PRCreationError: If any step of PR creation fails.
        """
        # Sanitize mapping name for use in branch and file names
        safe_name = self._sanitize_name(mapping_name)
        branch_name = f"mapping/{safe_name}"
        file_path = f"{self.MAPPINGS_FOLDER}/{safe_name}.ttl"

        logger.info(f"Creating PR for mapping: {mapping_name}")
        logger.debug(f"Branch: {branch_name}, File: {file_path}")

        try:
            # Get the base branch reference
            base_ref = self.repo.get_branch(base_branch)
            base_sha = base_ref.commit.sha
            logger.debug(f"Base branch {base_branch} at SHA: {base_sha[:8]}")

            # Create new branch
            self._create_branch(branch_name, base_sha)

            # Commit the RML file
            commit_message = f"Add RML mapping: {mapping_name}"
            self._commit_file(branch_name, file_path, rml_content, commit_message)

            # Create the PR
            pr = self._create_pr(
                title=f"Add mapping: {mapping_name}",
                body=description,
                head=branch_name,
                base=base_branch,
            )

            logger.info(f"Created PR #{pr.number}: {pr.html_url}")

            return PRResult(
                pr_number=pr.number,
                pr_url=pr.html_url,
                branch_name=branch_name,
            )

        except GithubException as e:
            logger.error(f"GitHub API error during PR creation: {e}")
            raise PRCreationError(f"Failed to create PR: {e}")
        except Exception as e:
            logger.error(f"Unexpected error during PR creation: {e}")
            raise PRCreationError(f"Failed to create PR: {e}")

    def _create_branch(self, branch_name: str, base_sha: str) -> None:
        """Create a new branch from a base commit.

        Args:
            branch_name: Name for the new branch.
            base_sha: SHA of the commit to branch from.

        Raises:
            GithubException: If branch creation fails.
        """
        ref_name = f"refs/heads/{branch_name}"

        # Check if branch already exists
        try:
            existing = self.repo.get_git_ref(f"heads/{branch_name}")
            logger.warning(f"Branch {branch_name} already exists, updating to new base")
            existing.edit(sha=base_sha)
        except GithubException:
            # Branch doesn't exist, create it
            self.repo.create_git_ref(ref=ref_name, sha=base_sha)
            logger.debug(f"Created branch: {branch_name}")

    def _commit_file(
        self,
        branch_name: str,
        file_path: str,
        content: str,
        message: str,
    ) -> None:
        """Commit a file to a branch.

        Args:
            branch_name: Branch to commit to.
            file_path: Path for the file in the repository.
            content: File content.
            message: Commit message.

        Raises:
            GithubException: If commit fails.
        """
        try:
            # Check if file already exists
            existing_file = self.repo.get_contents(file_path, ref=branch_name)
            # Update existing file
            self.repo.update_file(
                path=file_path,
                message=message,
                content=content,
                sha=existing_file.sha,
                branch=branch_name,
            )
            logger.debug(f"Updated file: {file_path}")
        except GithubException:
            # File doesn't exist, create it
            self.repo.create_file(
                path=file_path,
                message=message,
                content=content,
                branch=branch_name,
            )
            logger.debug(f"Created file: {file_path}")

    def _create_pr(
        self,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> Any:
        """Create a pull request.

        Args:
            title: PR title.
            body: PR description body.
            head: Head branch name.
            base: Base branch name.

        Returns:
            The created PullRequest object.

        Raises:
            GithubException: If PR creation fails.
        """
        pr = self.repo.create_pull(
            title=title,
            body=body,
            head=head,
            base=base,
        )
        return pr

    def _sanitize_name(self, name: str) -> str:
        """Sanitize a name for use in branch and file names.

        Args:
            name: Original name.

        Returns:
            Sanitized name with only alphanumeric characters and hyphens.
        """
        # Replace spaces and underscores with hyphens
        sanitized = name.replace(" ", "-").replace("_", "-")
        # Remove any characters that aren't alphanumeric or hyphens
        sanitized = "".join(c for c in sanitized if c.isalnum() or c == "-")
        # Remove consecutive hyphens
        while "--" in sanitized:
            sanitized = sanitized.replace("--", "-")
        # Remove leading/trailing hyphens
        sanitized = sanitized.strip("-")
        # Lowercase
        sanitized = sanitized.lower()
        # Ensure it's not empty
        if not sanitized:
            sanitized = "mapping"
        return sanitized
