You are operating inside an existing software repository.

Your task is to analyze this codebase and generate a set of Agent Skills for future coding agents working on this repository.

The skills must follow the Agent Skills specification exactly.

Documentation index:
- First fetch and read the complete Agent Skills documentation index from:
  https://agentskills.io/llms.txt
- Use it to discover any relevant pages before generating files.
- Also validate your output against the spec below.

Skill format requirements:
- Each skill is a directory.
- Each skill directory must contain a required SKILL.md file.
- SKILL.md must contain YAML frontmatter followed by Markdown instructions.
- The frontmatter must include:
  - name: required
  - description: required
- Optional fields may be used only when useful:
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
- Avoid vague descriptions like "helps with the codebase".

Directory structure:
skill-name/
├── SKILL.md
├── scripts/       optional
├── references/    optional
├── assets/        optional

Main objective:
Create practical, repository-specific skills that help an AI coding agent perform common work in this codebase correctly.

Do not create generic skills like:
- "typescript"
- "testing"
- "code-review"
- "debugging"
unless they are strongly customized to this repository’s actual architecture, commands, conventions, and pitfalls.

Instead, derive skills from the real codebase:
- architecture patterns
- domain concepts
- build/test/lint workflows
- deployment workflows
- database/indexer patterns
- smart contract patterns
- API/service boundaries
- frontend state/data-flow conventions
- generated code workflows
- common failure modes
- repository-specific naming conventions
- CI expectations
- local development setup
- migration procedures
- release procedures
- security-sensitive workflows

Analysis phase:
1. Inspect the repository structure.
2. Identify languages, frameworks, package managers, build tools, test tools, and deployment tooling.
3. Read key files, including where present:
   - README.md
   - package.json / pnpm-workspace.yaml / turbo.json
   - Cargo.toml / rust-toolchain.toml
   - foundry.toml / hardhat.config.*
   - Scarb.toml
   - Dockerfile / docker-compose.yml
   - Makefile / justfile
   - biome.json / eslint config / tsconfig
   - .github/workflows/*
   - database schema/migrations
   - contract directories
   - API/server entrypoints
   - frontend app entrypoints
   - existing docs
4. Infer the smallest useful set of skills.
5. Prefer 3-8 high-signal skills over many shallow skills.
6. Avoid duplicating information already obvious from the repo unless it is needed as an agent instruction.

Skill design rules:
Each skill should answer:
- When should an agent use this skill?
- What files should it inspect first?
- What commands should it run?
- What invariants must it preserve?
- What common mistakes should it avoid?
- What validation steps prove the task is done?

Each SKILL.md body should include, where applicable:
- Purpose
- When to use
- Repository map
- Standard workflow
- Validation commands
- Common pitfalls
- Escalation / uncertainty rules

Progressive disclosure:
- Keep SKILL.md concise and operational.
- Keep the main SKILL.md preferably under 500 lines.
- Move long references into references/*.md.
- Use scripts/ only when a reusable helper genuinely improves agent reliability.
- Reference files using relative paths from the skill root, for example:
  references/ARCHITECTURE.md
  scripts/check.sh

Do not:
- Invent tools, commands, or workflows that are not supported by the repository.
- Add speculative architecture.
- Copy huge chunks of source code into skill files.
- Create skills that only restate generic language knowledge.
- Create deeply nested reference chains.
- Put secrets, private keys, tokens, or sensitive environment values in skills.
- Modify application source code unless needed to add skill-related scripts/docs.
- Create more skills than necessary.

Output location:
Create the generated skills under:

.agent-skills/

Example:

.agent-skills/
├── local-dev/
│   └── SKILL.md
├── api-workflows/
│   └── SKILL.md
├── database-migrations/
│   ├── SKILL.md
│   └── references/
│       └── SCHEMA.md
└── ci-release/
    └── SKILL.md

Validation:
After generating the skills:
1. Check that every skill directory name matches its SKILL.md `name`.
2. Check that every name satisfies the naming rules.
3. Check that every description is concrete and within 1024 characters.
4. Check that every SKILL.md has valid YAML frontmatter.
5. If `skills-ref` is available, run:
   skills-ref validate .agent-skills/*
6. If `skills-ref` is not installed, perform a manual validation and report that the external validator was unavailable.

Final response:
Provide:
1. A list of generated skills.
2. A short explanation of why each skill exists.
3. Any assumptions made.
4. Validation results.
5. Any important repo areas that were not covered because the codebase lacked enough information.

Begin by analyzing the repository. Do not generate files until you have inspected the repo structure and key configuration files.

Additional harness constraints:

Generation staging targets:
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/llm-output/codex/skills
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/llm-output/claude/skills

Final runtime install targets:
- /Users/alexmetelli/source/horizon-starknet/.agents/skills
- /Users/alexmetelli/source/horizon-starknet/.claude/skills

Write generated repository-level skills only under every generation staging target listed above.
Do not write generated repository-level skills directly to the final runtime install targets.
After validation, the harness will install the validated generated skills into the final runtime install targets.

You may read the source repository here:
/Users/alexmetelli/source/horizon-starknet

You may write only to the listed generation staging targets and to this harness artifact directory for prompt/report files:
/Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet

Repository:
- repo_id: horizon-starknet
- name: horizon-starknet
- owner: ametel01
- url: https://github.com/ametel01/horizon-starknet
- local_path: ../../horizon-starknet
- default_branch: main
- generator: codex

Available scan artifacts:
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/onboarding-summary.md
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/unknowns.yml
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/scan/scan-manifest.yml
- /Users/alexmetelli/source/orgs-ai-harness/org-agent-skills/repos/horizon-starknet/scan/hypothesis-map.yml

Additional skill-shaping constraints:
- Prefer many small, specialized skills over a few broad general skills.
- Each skill must have highly specific `name` and `description` metadata so agents can select it without loading excess context.
- Descriptions must be trigger-focused and concrete, not marketing summaries.
- Avoid broad catch-all skills such as `repo-architecture` unless the repository truly has a narrow architecture workflow that needs it.
- Create the smallest useful skill set for this repo, normally 3-8 targeted skills.
- If multiple generation staging targets are listed, write the same generated skill set to each target.
