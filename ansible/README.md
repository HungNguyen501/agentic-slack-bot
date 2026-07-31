# Ansible — Deploying to the Remote VM

This folder automates deploying, restarting, and inspecting the bot on the remote
staging VM (`vm-ai-job-2`). If you've never used Ansible before, read this top to
bottom once — it'll make the rest of the folder self-explanatory.

## What Ansible actually does here

Ansible connects to a remote machine and runs a list of steps ("tasks") on it —
things like "copy this file," "run this shell command," "make sure this directory
exists." A **playbook** is a YAML file listing which tasks to run and on which
hosts. There's no agent installed on the VM; Ansible just opens a connection
(here, via AWS SSM — see below) and executes commands over it.

Every command in this folder follows the same shape:

```bash
ansible-playbook playbooks/<file>.yml -i inventory/hosts.ini
```

You'll rarely type this directly — the [Makefile](../Makefile) wraps each playbook
in a `make ansible-*` target (`make ansible-deploy`, `make ansible-restart`, etc.).
Run `make help` from the repo root to see all of them with examples.

## Folder structure

```
ansible/
  ansible.cfg                  # Ansible's own settings for this project
  inventory/
    hosts.ini                  # WHICH machine(s) to connect to, and HOW
    group_vars/all/vars.yml    # Variables applied to every host (image tag, worker count, ...)
  playbooks/
    deploy.yml                 # Full deploy: copy files, pull image, start services
    stop.yml                   # docker compose down on the remote host
    sync-skills.yml            # Push src/worker/skills/ only, no redeploy
    logs.yml                   # Fetch docker compose logs from the remote host
  roles/
    slack_bot_deploy/          # Reusable set of tasks that deploy.yml runs
      tasks/main.yml           # The actual steps: copy files, docker compose up, ...
      defaults/main.yml        # Fallback values for role variables
      templates/
        docker-compose.prod.yml.j2  # Compose override, rendered with real values
```

### `inventory/` — which machine(s), and how to reach them

The **inventory** is Ansible's address book: it tells `ansible-playbook` which
servers exist and how to log into them. Ours is [`hosts.ini`](inventory/hosts.ini):

```ini
[staging]
vm-ai-job-2 ansible_aws_ssm_instance_id=i-0d845b5bbf628b67a

[staging:vars]
ansible_connection=community.aws.aws_ssm
...
ansible_become=true
ansible_become_user=root
```

- `[staging]` is a **group** containing one host, `vm-ai-job-2`. Every playbook in
  this repo targets `hosts: all`, which currently means "everything in the
  inventory" — just this one VM.
- `ansible_connection=community.aws.aws_ssm` means Ansible does **not** SSH in.
  It uses AWS Systems Manager Session Manager to open a shell on the instance
  (`i-0d845b5bbf628b67a`), using your local AWS credentials. No SSH keys, no open
  port 22 required — but you do need working AWS CLI credentials and the
  `session-manager-plugin` installed locally.
- `ansible_become=true` + `ansible_become_user=root` means every task runs via
  `sudo` on the remote host (needed since the deploy directory and Docker are
  root-owned).

`inventory/group_vars/all/vars.yml` holds variables that apply to **every host in
the inventory** (hence `all`). Right now it pins the image tag and worker count
used by every deploy unless overridden on the command line:

```yaml
slack_bot_deploy_dir: /var/workspace/agentic-slack-bot
slack_bot_deploy_image_repo: hungwnguyen/agentic-slack-bot
slack_bot_deploy_image_tag: v2026.06.28
slack_bot_deploy_worker_count: "4"
```

**Impact if you edit this file:** the next `ansible-deploy` will pull whatever
image tag / worker count you set here. There's no confirmation step — treat it
like a production config file.

### `playbooks/` — what to do, and when

A playbook answers two questions: which hosts, and which tasks/roles to run
against them. Each file here is one operation you can trigger from the Makefile:

| Playbook | Make target | What it does |
|---|---|---|
| [`deploy.yml`](playbooks/deploy.yml) | `make ansible-deploy` | Runs the whole `slack_bot_deploy` role: copies files, renders the prod compose override, pulls the image, starts services, then deletes the remote `.env`. Also has a `--check` dry-run mode (`make ansible-deploy-check`) and a `deploy_restart`-only mode (`make ansible-restart`) via tags — see below. |
| [`stop.yml`](playbooks/stop.yml) | `make ansible-stop` | Runs `docker compose down --remove-orphans` on the remote host. Stops everything; does not remove the deploy directory. |
| [`sync-skills.yml`](playbooks/sync-skills.yml) | `make ansible-sync-skills` | Copies **only** `src/worker/skills/` to the remote host. Use this for skill-file edits that don't need a full redeploy — services will pick up changes on their next request since skills are volume-mounted, no restart required. |
| [`logs.yml`](playbooks/logs.yml) | `make ansible-logs` | Runs `docker compose logs --tail N <service>` remotely and prints the output locally. Pass `SVC=worker TAIL=200` to the make target to scope it. |

`deploy.yml` also has a **pre-task** that fails fast, before touching the remote
host at all, if your local `.env` file doesn't exist — so you can't accidentally
deploy with a missing secrets file.

**Impact if you edit these:** these files change what actually happens on the VM.
`stop.yml` takes the bot offline in Slack. `deploy.yml` replaces running
containers. Test risky changes with `make ansible-deploy-check` first (it runs in
`--check` mode, which reports what *would* change without changing anything —
though `command:` tasks like `docker compose up` can't be truly dry-run, so read
the diff output critically).

### `roles/` — the reusable "how"

A **role** is a named, reusable bundle of tasks + default variables + template
files, meant to be plugged into one or more playbooks. We have one role,
[`slack_bot_deploy`](roles/slack_bot_deploy/), and `deploy.yml` runs it via:

```yaml
roles:
  - role: slack_bot_deploy
```

Inside the role:

- **`tasks/main.yml`** — the actual ordered list of steps. Reading top to bottom,
  it: ensures the deploy directory exists → copies `docker-compose.yml` → copies
  the skills directory → copies your local `.env` → renders
  `docker-compose.prod.yml` from the template → `docker compose pull` → 
  `docker compose up -d --force-recreate` → deletes the remote `.env` → (if the
  `deploy_restart` tag is used instead) just restarts the containers without
  re-pulling.
- **`defaults/main.yml`** — fallback values for every `slack_bot_deploy_*`
  variable used in `tasks/main.yml`, used only if `group_vars` or a `-e` flag
  doesn't override them. Lowest-priority variable source.
- **`templates/docker-compose.prod.yml.j2`** — a Jinja2 template. Ansible fills in
  `{{ slack_bot_deploy_image_repo }}` and `{{ slack_bot_deploy_image_tag }}` with
  real values and writes the result to
  `/var/workspace/agentic-slack-bot/docker-compose.prod.yml` on the VM. This is
  the override file that makes `receiver`/`worker`/`scheduler` pull a pre-built
  image from Docker Hub instead of building from source on the server.

**Tags** (`deploy_init`, `deploy_update`, `deploy_restart`) mark which tasks run
for which use case. `make ansible-restart` passes `--tags deploy_restart`, which
skips every other task in the file — including the `.env` copy and delete — and
only restarts the already-running containers.

**Impact if you edit `tasks/main.yml`:** you are changing what every deploy
*does*, in order. Get the order wrong (e.g. deleting `.env` before `docker
compose up` runs) and the next deploy will silently break — `${VAR}` references
in `docker-compose.yml` will resolve to empty strings instead of failing loudly.

### `ansible.cfg` — project-wide settings

```ini
[defaults]
inventory = hosts
ssh_common_args='-F ~/.ssh/config'
roles_path = roles:/etc/ansible/roles
collections_path = .ansible/collections:~/.ansible/collections
pipelining = True
deprecation_warnings = False
```

- `inventory = hosts` is a stale default — every command in this repo passes
  `-i inventory/hosts.ini` explicitly, which overrides this. Don't rely on the
  default; always pass `-i` (the Makefile targets already do).
- `roles_path` tells Ansible where to look for roles named in a playbook (our
  local `roles/` directory, checked first).
- `pipelining = True` is a performance setting (fewer SSH/SSM round-trips per
  task); not something you need to touch.

## Secrets handling

`.env` (with `OPENAI_API_KEY`, `DATABRICKS_ACCESS_TOKEN`, `SUPABASE_DB_URL`, etc.)
is copied to the VM only long enough for `docker compose up` to bake those values
into each container's config, then deleted by the role's last `deploy_init`/
`deploy_update` task. It is **not** present on disk on the VM between deploys.
`make ansible-restart` never touches `.env` at all — it only restarts containers
that already have their environment set from the last full deploy.

## Typical workflows

```bash
# Full deploy with the currently pinned image tag
make ansible-deploy

# Preview what a deploy would change, without touching the VM
make ansible-deploy-check

# Ship a new image tag
make docker-build-push TAG=v2026.08.01
# then edit slack_bot_deploy_image_tag in inventory/group_vars/all/vars.yml, or run
# ansible-playbook directly with an override:
cd ansible && ansible-playbook playbooks/deploy.yml -i inventory/hosts.ini -e slack_bot_deploy_image_tag=v2026.08.01

# Edited a skill .md file, want it live without a full redeploy
make ansible-sync-skills

# Something looks wrong, check recent worker logs
make ansible-logs SVC=worker TAIL=200

# Take everything down
make ansible-stop
```
