"""
GAM Branch Management - Git-Native Agent Memory Branching

Implements the hierarchical branching model:
- main: Root agent, tenant-wide stable memory
- c-suite/*: Long-lived executive branches
- project/*: Isolated project memory spaces  
- feature/*: Ephemeral sub-agent branches
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml
from git import Repo

from .core import GAMRepository


class BranchLevel(Enum):
    """Hierarchy levels for branches."""
    MAIN = "main"
    CSUITE = "c-suite"
    PROJECT = "project"
    FEATURE = "feature"


@dataclass
class BranchInfo:
    """Information about a GAM branch."""
    name: str
    level: BranchLevel
    parent: Optional[str]
    created_at: Optional[datetime] = None
    created_by: Optional[str] = None
    description: str = ""
    is_active: bool = True
    
    @property
    def short_name(self) -> str:
        """Get the branch name without prefix."""
        if "/" in self.name:
            return self.name.split("/", 1)[1]
        return self.name


# Branch naming patterns
BRANCH_PATTERNS = {
    BranchLevel.MAIN: r"^(main|master)$",
    BranchLevel.CSUITE: r"^c-suite/[a-z][a-z0-9-]*$",
    BranchLevel.PROJECT: r"^project/[a-z][a-z0-9-]*$",
    BranchLevel.FEATURE: r"^feature/[a-z][a-z0-9-]*$",
}


def parse_branch_level(name: str) -> Optional[BranchLevel]:
    """Determine the level of a branch from its name."""
    for level, pattern in BRANCH_PATTERNS.items():
        if re.match(pattern, name):
            return level
    return None


def validate_branch_name(name: str, level: BranchLevel) -> tuple[bool, str]:
    """Validate a branch name matches the expected pattern."""
    pattern = BRANCH_PATTERNS[level]
    
    if re.match(pattern, name):
        return True, ""
    
    examples = {
        BranchLevel.MAIN: "main",
        BranchLevel.CSUITE: "c-suite/cfo, c-suite/cto",
        BranchLevel.PROJECT: "project/mobile-launch, project/ai-platform",
        BranchLevel.FEATURE: "feature/landing-page, feature/api-integration",
    }
    
    return False, f"Invalid branch name. Examples: {examples[level]}"


def get_parent_branch(level: BranchLevel, project: Optional[str] = None, repo: Optional[Repo] = None) -> str:
    """Determine the parent branch for a given level."""
    # Determine root branch name (main or master)
    root = "main"
    if repo:
        branch_names = [b.name for b in repo.branches]
        if "main" in branch_names:
            root = "main"
        elif "master" in branch_names:
            root = "master"
    
    if level == BranchLevel.MAIN:
        return ""  # No parent
    elif level == BranchLevel.CSUITE:
        return root
    elif level == BranchLevel.PROJECT:
        return root
    elif level == BranchLevel.FEATURE:
        if project:
            return f"project/{project}"
        return root  # Fallback
    return root


class BranchManager:
    """Manages GAM branch hierarchy."""
    
    def __init__(self, repo: GAMRepository):
        self.repo = repo
        self.git_repo = repo.repo
        self.branches_file = repo.gam_dir / "branches.yaml"
        self._load_config()
    
    def _load_config(self):
        """Load branch configuration."""
        if self.branches_file.exists():
            with open(self.branches_file) as f:
                self.config = yaml.safe_load(f) or {}
        else:
            self.config = {"branches": {}}
    
    def _save_config(self):
        """Save branch configuration."""
        with open(self.branches_file, "w") as f:
            yaml.dump(self.config, f, default_flow_style=False)
    
    def create_branch(
        self,
        name: str,
        level: BranchLevel,
        description: str = "",
        parent: Optional[str] = None,
        project: Optional[str] = None,
    ) -> BranchInfo:
        """
        Create a new branch following the hierarchy rules.
        
        Args:
            name: Short name (will be prefixed based on level)
            level: Branch level (csuite, project, feature)
            description: Human-readable description
            parent: Override parent branch
            project: For feature branches, the project they belong to
        """
        # Build full branch name
        if level == BranchLevel.MAIN:
            full_name = "main"
        else:
            prefix = level.value
            full_name = f"{prefix}/{name}"
        
        # Validate
        valid, error = validate_branch_name(full_name, level)
        if not valid:
            raise ValueError(error)
        
        # Check if branch already exists
        existing = [b.name for b in self.git_repo.branches]
        if full_name in existing:
            raise ValueError(f"Branch '{full_name}' already exists")
        
        # Determine parent
        if parent is None:
            parent = get_parent_branch(level, project, self.git_repo)
        
        # Ensure parent exists
        if parent and parent not in existing:
            raise ValueError(f"Parent branch '{parent}' does not exist")
        
        # Create the branch
        if parent:
            parent_ref = self.git_repo.branches[parent]
            new_branch = self.git_repo.create_head(full_name, parent_ref.commit)
        else:
            new_branch = self.git_repo.create_head(full_name)
        
        # Store metadata
        now = datetime.now(timezone.utc)
        branch_info = BranchInfo(
            name=full_name,
            level=level,
            parent=parent,
            created_at=now,
            created_by="gam",
            description=description,
            is_active=True,
        )
        
        self.config["branches"][full_name] = {
            "level": level.value,
            "parent": parent,
            "created_at": now.isoformat(),
            "description": description,
            "is_active": True,
        }
        self._save_config()
        
        return branch_info
    
    def list_branches(self, level: Optional[BranchLevel] = None) -> list[BranchInfo]:
        """List all branches, optionally filtered by level."""
        branches = []
        
        for branch in self.git_repo.branches:
            branch_level = parse_branch_level(branch.name)
            
            if level and branch_level != level:
                continue
            
            # Get metadata from config
            meta = self.config.get("branches", {}).get(branch.name, {})
            
            branches.append(BranchInfo(
                name=branch.name,
                level=branch_level or BranchLevel.MAIN,
                parent=meta.get("parent"),
                created_at=datetime.fromisoformat(meta["created_at"]) if meta.get("created_at") else None,
                description=meta.get("description", ""),
                is_active=meta.get("is_active", True),
            ))
        
        return sorted(branches, key=lambda b: (b.level.value, b.name))
    
    def get_branch(self, name: str) -> Optional[BranchInfo]:
        """Get info about a specific branch."""
        if name not in [b.name for b in self.git_repo.branches]:
            return None
        
        level = parse_branch_level(name)
        meta = self.config.get("branches", {}).get(name, {})
        
        return BranchInfo(
            name=name,
            level=level or BranchLevel.MAIN,
            parent=meta.get("parent"),
            created_at=datetime.fromisoformat(meta["created_at"]) if meta.get("created_at") else None,
            description=meta.get("description", ""),
            is_active=meta.get("is_active", True),
        )
    
    def get_hierarchy(self) -> dict:
        """Get the full branch hierarchy as a tree."""
        branches = self.list_branches()
        
        tree = {"main": {"children": {}}}
        
        for branch in branches:
            if branch.name == "main":
                continue
            
            parent = branch.parent or "main"
            
            # Navigate to parent in tree
            parts = parent.split("/") if "/" in parent else [parent]
            current = tree
            for part in parts:
                if part in current:
                    current = current[part].get("children", {})
                elif part == parent:
                    current = tree.get("main", {}).get("children", {})
            
            # Add this branch
            if branch.level == BranchLevel.CSUITE:
                tree["main"]["children"][branch.name] = {
                    "info": branch,
                    "children": {}
                }
            elif branch.level == BranchLevel.PROJECT:
                tree["main"]["children"][branch.name] = {
                    "info": branch,
                    "children": {}
                }
            elif branch.level == BranchLevel.FEATURE:
                # Find project parent
                if branch.parent and branch.parent.startswith("project/"):
                    proj = branch.parent
                    if proj in tree["main"]["children"]:
                        tree["main"]["children"][proj]["children"][branch.name] = {
                            "info": branch,
                            "children": {}
                        }
        
        return tree
    
    def archive_branch(self, name: str, reason: str = "") -> bool:
        """Archive a branch (mark inactive, optionally delete)."""
        if name not in [b.name for b in self.git_repo.branches]:
            return False
        
        if name in self.config.get("branches", {}):
            self.config["branches"][name]["is_active"] = False
            self.config["branches"][name]["archived_at"] = datetime.now(timezone.utc).isoformat()
            self.config["branches"][name]["archive_reason"] = reason
            self._save_config()
        
        return True
    
    def checkout(self, name: str) -> bool:
        """Switch to a branch."""
        if name not in [b.name for b in self.git_repo.branches]:
            return False
        
        self.git_repo.heads[name].checkout()
        return True
    
    def current_branch(self) -> str:
        """Get the current branch name."""
        return self.git_repo.active_branch.name
