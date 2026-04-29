You are operating with access to a GitHub organization or GitHub account containing multiple repositories.

Your task is to analyze the organization’s repositories and generate a set of Agent Skills that capture shared engineering practices, conventions, workflows, and standards that apply across multiple repos.

These are NOT single-repository skills. They should describe reusable practices across the org/account.

The skills must follow the Agent Skills specification exactly.

Documentation index:
- First fetch and read the complete Agent Skills documentation index from:
  https://agentskills.io/llms.txt
- Use this file to discover relevant pages before generating files.
- Validate your output against the Agent Skills spec.

Output location:
Create generated skills under:

.github/agent-skills/

Example:

.github/agent-skills/
├── pull-request-workflow/
│   └── SKILL.md
├── typescript-service-standards/
│   └── SKILL.md
├── ci-and-release/
│   └── SKILL.md
├── security-and-secrets/
│   └── SKILL.md
└── documentation-style/
    └── SKILL.md

Skill format requirements:
- Each skill is a directory.
- Each skill directory must contain a required SKILL.md file.
- SKILL.md must contain YAML frontmatter followed by Markdown instructions.
- Required frontmatter:
  - name
  - description
- Optional frontmatter may be used only when useful:
  - license
  - compatibility
  - metadata
  - allowed-tools

Name rules:
- name must match the parent directory name.
- name must be 1-64 characters.
- lowercase letters, numbers, and hyphens only.
- must not start or end with a hyphen.
- must not contain consecutive hyphens.

Description rules:
- 1-1024 characters.
- Must explain what the skill does and when to use it.
- Include concrete keywords that help an agent decide when the skill applies.
- Avoid vague descriptions like “helps with repos”.

Main objective:
Create practical, organization-wide Agent Skills that help future AI coding agents work consistently across this GitHub org/account.

These skills should encode:
- repeated engineering standards
- shared repository structure patterns
- common CI/CD conventions
- shared review and merge expectations
- common testing policies
- security and secrets handling rules
- documentation conventions
- release/versioning conventions
- language/framework standards that appear across multiple repos
- package manager conventions
- issue/PR workflows
- shared deployment assumptions
- common naming conventions
- common agent operating rules

Do NOT create skills that only apply to one repo unless that repo clearly acts as the canonical template or reference implementation for the org.

Analysis phase:
1. List repositories in the org/account.
2. Select a representative sample of active, relevant repos.
3. Prefer recently updated and non-archived repos.
4. Inspect shared files across repos, including where present:
   - README.md
   - CONTRIBUTING.md
   - CODEOWNERS
   - SECURITY.md
   - LICENSE
   - package.json
   - pnpm-workspace.yaml
   - turbo.json
   - Cargo.toml
   - rust-toolchain.toml
   - foundry.toml
   - hardhat.config.*
   - Scarb.toml
   - Dockerfile
   - docker-compose.yml
   - Makefile
   - justfile
   - biome.json
   - eslint config
   - prettier config
   - tsconfig.json
   - .editorconfig
   - .github/workflows/*
   - .github/pull_request_template.md
   - .github/ISSUE_TEMPLATE/*
   - release/changelog files
   - docs directories

5. Identify conventions that appear in multiple repos.
6. Distinguish:
   - org-wide standards
   - language-family standards
   - repo-specific exceptions
7. Prefer observed conventions over assumptions.
8. Where evidence is weak, mark guidance as conditional rather than universal.

Skill selection rules:
Create 4-10 high-signal org-wide skills.

Good org-wide skills may include:
- github-pr-workflow
- ci-quality-gates
- release-and-versioning
- documentation-standards
- security-and-secrets
- typescript-repo-standards
- rust-repo-standards
- solidity-contract-standards
- cairo-starknet-standards
- agent-coding-workflow
- repo-bootstrapping
- testing-standards

Do NOT create:
- one skill per repository
- generic language tutorials
- skills based on a single isolated example
- skills that merely summarize README files
- skills that contradict observed repo practice
- skills that require unavailable tools
- skills with speculative policies not supported by the repos

Each skill should answer:
- When should an agent use this skill?
- Which repos/files demonstrate this convention?
- What workflow should the agent follow?
- What commands are commonly used?
- What invariants should be preserved?
- What mistakes should be avoided?
- What validation steps should be run?
- What repo-specific exceptions are known?

Each SKILL.md body should include, where applicable:
- Purpose
- When to use
- Evidence from repositories
- Standard workflow
- Commands
- Required checks
- Common pitfalls
- Exceptions
- Escalation / uncertainty rules

Progressive disclosure:
- Keep SKILL.md concise and operational.
- Keep the main SKILL.md preferably under 500 lines.
- Move longer evidence tables or detailed references into references/*.md.
- Use references/ only when they improve clarity.
- Use scripts/ only when a reusable helper genuinely improves agent reliability.
- Reference files using relative paths from the skill root, for example:
  references/EVIDENCE.md
  scripts/check.sh

Evidence requirements:
For every generated skill, include an “Evidence from repositories” section listing:
- repo names inspected
- files that justify the convention
- whether the convention is strong, moderate, or weak

Use this evidence strength scale:
- Strong: appears consistently across many relevant repos.
- Moderate: appears across several repos, but with variation.
- Weak: appears in one or two repos, or appears to be emerging.

If evidence is weak, the skill must phrase guidance as conditional:
- “Use this when working in repos that follow this pattern...”
- “Check the target repo before applying this...”
- “Do not assume this applies globally...”

Conflict handling:
If repositories disagree:
1. Do not force a fake universal rule.
2. Document the variants.
3. Prefer a skill that helps the agent detect which variant the target repo uses.
4. Include a decision table if useful.

Example conflict handling:
- Some repos use pnpm, others use npm.
- Some repos use Foundry, others use Hardhat.
- Some repos use Biome, others use ESLint/Prettier.
- Some repos use semantic-release, others use manual changelogs.

In these cases, write a skill like:
- package-manager-detection
- evm-contract-tooling
- lint-format-standards
rather than pretending there is one global command.

Validation:
After generating skills:
1. Verify every skill directory name matches SKILL.md `name`.
2. Verify every name satisfies the naming rules.
3. Verify every description is concrete and within 1024 characters.
4. Verify every SKILL.md has valid YAML frontmatter.
5. Verify every skill has an “Evidence from repositories” section.
6. Verify no skill is purely generic.
7. If `skills-ref` is available, run:
   skills-ref validate .github/agent-skills/*
8. If `skills-ref` is unavailable, perform manual validation and report that the external validator was unavailable.

Final response:
Provide:
1. List of generated skills.
2. Why each skill exists.
3. Evidence strength for each skill.
4. Repositories sampled.
5. Validation results.
6. Conflicts or variants discovered.
7. Important areas not covered due to insufficient evidence.

Begin by analyzing the GitHub org/account. Do not generate files until you have inspected multiple repositories and identified repeated conventions.

Additional harness constraints:

Generation staging targets:
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/org/llm-output/codex/skills
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/org/llm-output/claude/skills

Final global runtime install targets:
- /Users/alexmetelli/.agents/skills
- /Users/alexmetelli/.claude/skills

Write organization-level skills only under every generation staging target listed above.
Do not write organization-level skills directly to the final global runtime install targets.
After validation, the harness will install the validated generated skills into the final global runtime install targets.
Do not write org-level skills under `.github/agent-skills/` unless that path is also explicitly listed above.

Registered repositories available through the harness:
- orgs-ai-harness: local_path=.., url=https://github.com/ametel01/orgs-ai-harness, status=selected, active=true
- vitals-db: local_path=../../vitals-db, url=https://github.com/ametel01/vitals-db, status=selected, active=true
- horizon-starknet: local_path=../../horizon-starknet, url=https://github.com/ametel01/horizon-starknet, status=selected, active=true
- agent-vitals: local_path=../../agent-vitals, url=https://github.com/ametel01/agent-vitals, status=selected, active=true
- agents-toolbelt: local_path=../../agents-toolbelt, url=https://github.com/ametel01/agents-toolbelt, status=selected, active=true

Org pack root:
/Users/alexmetelli/source/orgs-ai-harness/org-agent-skills

Additional skill-shaping constraints:
- Prefer many small, specialized skills over a few broad general skills.
- Create targeted org-level skills with precise trigger metadata.
- Skill descriptions are critical routing metadata; make them concrete and narrow so agents avoid unnecessary context loading.
- Do not create one large "org practices" skill.
- If evidence is weak or repo conventions conflict, create a detector/decision skill rather than a universal policy skill.
