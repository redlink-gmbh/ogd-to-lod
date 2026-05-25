"""GitHub service for branch creation and PR management."""

from dataclasses import dataclass
from typing import Any

from github import Auth, Github, GithubException
from github.Repository import Repository

from ogd_to_lod._slug import slugify
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

    def __init__(self, config: GitHubConfig):
        """Initialize the GitHub service.

        Args:
            config: GitHub configuration with repo and token.
        """
        self._config = config
        self._client = Github(auth=Auth.Token(config.token))
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
        output_folder: str,
        csv_filename: str,
        csv_content: str,
        metadata_content: str | None = None,
        base_branch: str = "main",
        mappings_folder: str = "mapping",
    ) -> PRResult:
        """Create a PR with a new RML mapping.

        Creates a new branch, commits the YARRRML mapping file and the CSV
        source file (and optionally context/metadata files), and opens a
        pull request.

        Args:
            mapping_name: Name for the mapping (used in PR title and commit messages).
            rml_content: The YARRRML mapping content to commit.
            description: Human-readable description for the PR body.
            output_folder: Subfolder name within the mappings parent folder.
            csv_filename: Filename for the CSV file in the repository.
            csv_content: Content of the CSV file to commit.
            metadata_content: Optional static metadata Turtle (cube:Cube +
                ObservationSet) to commit as ``metadata.ttl``.
            base_branch: Branch to create PR against (default: main).
            mappings_folder: Parent folder in the repository (default: mapping).

        Returns:
            PRResult with PR number, URL, and branch name.

        Raises:
            PRCreationError: If any step of PR creation fails.
        """
        # Sanitize output folder for use in branch and file paths
        safe_folder = self._sanitize_name(output_folder)
        branch_name = f"mapping/{safe_folder}"
        folder_path = f"{mappings_folder}/{safe_folder}"
        yarrrml_path = f"{folder_path}/mapping.yarrrml.yaml"
        csv_path_in_repo = f"{folder_path}/{csv_filename}"

        logger.info(f"Creating PR for mapping: {mapping_name}")
        logger.debug(f"Branch: {branch_name}, Folder: {folder_path}")

        try:
            # Get the base branch reference
            base_ref = self.repo.get_branch(base_branch)
            base_sha = base_ref.commit.sha
            logger.debug(f"Base branch {base_branch} at SHA: {base_sha[:8]}")

            # Create new branch
            self._create_branch(branch_name, base_sha)

            # Commit the YARRRML mapping file
            self._commit_file(
                branch_name, yarrrml_path, rml_content,
                f"Add YARRRML mapping: {mapping_name}"
            )

            # Commit the CSV source file
            self._commit_file(
                branch_name, csv_path_in_repo, csv_content,
                f"Add CSV source: {csv_filename}"
            )
            logger.debug(f"Committed CSV file: {csv_path_in_repo}")

            # Optionally commit the static metadata Turtle
            if metadata_content:
                metadata_path = f"{folder_path}/metadata.ttl"
                self._commit_file(
                    branch_name, metadata_path, metadata_content,
                    f"Add static metadata: {mapping_name}"
                )
                logger.debug(f"Committed metadata file: {metadata_path}")

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

        Uses an EAFP pattern: tries to create the ref first, and if it
        already exists (422), falls back to updating the existing ref.

        Args:
            branch_name: Name for the new branch.
            base_sha: SHA of the commit to branch from.

        Raises:
            GithubException: If branch creation fails for a reason other
                than the branch already existing.
        """
        ref_name = f"refs/heads/{branch_name}"

        try:
            self.repo.create_git_ref(ref=ref_name, sha=base_sha)
            logger.debug(f"Created branch: {branch_name}")
        except GithubException as e:
            if e.status == 422:
                # Branch already exists — update it to the new base
                existing = self.repo.get_git_ref(f"heads/{branch_name}")
                existing.edit(sha=base_sha, force=True)
                logger.warning(f"Branch {branch_name} already existed, reset to {base_sha[:8]}")
            else:
                raise

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
        """Create a pull request, or update the existing one if already open.

        Args:
            title: PR title.
            body: PR description body.
            head: Head branch name.
            base: Base branch name.

        Returns:
            The created or updated PullRequest object.

        Raises:
            GithubException: If PR creation/update fails.
        """
        try:
            pr = self.repo.create_pull(
                title=title,
                body=body,
                head=head,
                base=base,
            )
            return pr
        except GithubException as e:
            if e.status == 422:
                # PR likely already exists — find and update it
                pr = self._find_existing_pr(head, base)
                if pr is not None:
                    pr.edit(title=title, body=body)
                    logger.warning(f"Updated existing PR #{pr.number} for branch {head}")
                    return pr
            raise

    def _find_existing_pr(self, head: str, base: str) -> Any | None:
        """Find an open PR from head to base.

        Args:
            head: Head branch name.
            base: Base branch name.

        Returns:
            The PullRequest object if found, None otherwise.
        """
        pulls = self.repo.get_pulls(state="open", head=f"{self.repo.owner.login}:{head}", base=base)
        for pr in pulls:
            return pr
        return None

    def _sanitize_name(self, name: str) -> str:
        """Sanitize a name for use in branch and file names."""
        return slugify(name)
