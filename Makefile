# Makefile — stronghold workflow entry points.
#
# Recurring operations go through `make` so a single allow rule covers them (see CLAUDE.md
# "Makefile-First Command Policy"). Add targets here rather than allowlisting raw command shapes.

.DEFAULT_GOAL := help
.PHONY: help facts sync mocks mocks-stop workflow-lint plan-audit plan-sweep wt-new wt-ls wt-rm wt-capacity

SYNC := .claude/scripts/stronghold-sync.sh
MOCKSERVE := .claude/scripts/mock-server.sh
FACTS := .claude/scripts/repo-facts.sh
WT := .claude/scripts/wt.sh
WT_CAPACITY := .claude/scripts/wt-capacity.sh
WORKFLOW_LINT := .claude/scripts/workflow-lint.sh
MSG ?= chore(stronghold): manual sync

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

facts: ## Cross-repo metadata digest: seams, branches, commands, make targets (REPO=<name> to scope)
	@$(FACTS) $(REPO) $(ARGS)

# REPO/BRANCH reach wt.sh through the environment, never through make's recipe text: make splices
# $(VAR) into the recipe BEFORE the shell parses it, so "$(BRANCH)" would let a valid git ref like
# a$(id)b run code. The recipes expand "$$WT_REPO"/"$$WT_BRANCH" in the shell instead.
wt-new wt-ls wt-rm: export WT_REPO := $(REPO)
wt-new wt-ls wt-rm: export WT_BRANCH := $(BRANCH)
wt-new wt-rm: export WT_INIT := $(INIT)

wt-new: ## Create a worktree: make wt-new REPO=<repo> BRANCH=<branch> [INIT=1 for a repo with no worktree support yet]
	@$(WT) new $${WT_INIT:+--init} "$$WT_REPO" "$$WT_BRANCH"

wt-ls: ## List worktrees (REPO=<name> to scope)
	@$(WT) ls $${WT_REPO:+"$$WT_REPO"}

wt-rm: ## Remove a worktree: make wt-rm REPO=<repo> BRANCH=<branch>
	@$(WT) rm $${WT_INIT:+--init} "$$WT_REPO" "$$WT_BRANCH"

wt-capacity: export WT_REPO := $(REPO)
wt-capacity: export WT_ARGS := $(ARGS)
wt-capacity: ## Machine-wide worktree capacity (read-only)
	@set -f; $(WT_CAPACITY) $${WT_REPO:+"$$WT_REPO"} $$WT_ARGS

sync: ## Commit + push stronghold changes to the default branch (MSG="..." for a message)
	@$(SYNC) "$(MSG)"

mocks: ## Serve ./plans on 127.0.0.1 so a browser tool can screenshot mock HTML (file:// is blocked)
	@$(MOCKSERVE) start $(PORT)

mocks-stop: ## Stop the local mock server
	@$(MOCKSERVE) stop

workflow-lint: export WL_REPO := $(REPO)
workflow-lint: export STRONGHOLD_DIR := $(STRONGHOLD_DIR)
workflow-lint: ## Lint seams, worktrees, plans, hooks against the documented rules (REPO=<name>)
	@$(WORKFLOW_LINT) $${WL_REPO:+--repo "$$WL_REPO"}

plan-audit: export PL_REPO := $(REPO)
plan-audit: ## Report completed plans with open issues + open topics holding no plan (REPO=<name>); exit 1 on drift
	@.claude/scripts/plan-lifecycle.sh audit $${PL_REPO:+"$$PL_REPO"}

plan-sweep: export PL_REPO := $(REPO)
plan-sweep: ## Move plan-less (review-only) open topics to completed/ (REPO=<name>)
	@.claude/scripts/plan-lifecycle.sh sweep $${PL_REPO:+"$$PL_REPO"}
