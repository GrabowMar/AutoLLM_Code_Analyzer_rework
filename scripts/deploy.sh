#!/usr/bin/env bash
# =============================================================================
# deploy.sh — AutoLLM Code Analyzer · one-shot deployment script
# =============================================================================
#
# Handles both first-time installs and in-place updates.
#
# USAGE
#   From inside a cloned repo:
#     ./scripts/deploy.sh
#
#   One-liner install (fetches the repo automatically):
#     bash <(curl -fsSL https://raw.githubusercontent.com/GrabowMar/AutoLLM_Code_Analyzer_rework/main/scripts/deploy.sh)
#
# CONFIGURATION  (all optional — script prompts for required values when run
#                 interactively; use env vars for CI / automated runs)
#
#   DOMAIN              Public hostname, e.g. myapp.example.com  [required]
#   DEPLOY_DIR          Install / update path
#                       (default: ~/AutoLLM_Code_Analyzer_rework)
#   REPO_URL            Git remote URL  (default: canonical GitHub URL)
#   BRANCH              Git branch to track  (default: main)
#   OPENROUTER_KEY      Optional global OpenRouter API key.
#                       Leave empty — users can set their own key in the UI.
#   CADDY_CONTAINER     Running Caddy container name (auto-detected if empty)
#   NO_PROXY            Set to 1 to skip reverse-proxy configuration entirely
#                       (app will be reachable only on its raw ports)
#   CI                  Set to 1 / true for non-interactive mode
#                       (requires DOMAIN to be set via env var)
#
# =============================================================================
set -euo pipefail
IFS=$'\n\t'

# ── colour helpers ─────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m' YELLOW='\033[1;33m' GREEN='\033[0;32m'
  CYAN='\033[0;36m' BOLD='\033[1m' DIM='\033[2m' RESET='\033[0m'
else
  RED='' YELLOW='' GREEN='' CYAN='' BOLD='' DIM='' RESET=''
fi

_ts()      { date '+%Y-%m-%d %H:%M:%S'; }
info()     { echo -e "${CYAN}[deploy]${RESET} $*";              echo "$(_ts) INFO  $*" >> "${LOG_FILE:-/tmp/deploy.log}"; }
success()  { echo -e "${GREEN}[deploy]${RESET} $*";             echo "$(_ts) OK    $*" >> "${LOG_FILE:-/tmp/deploy.log}"; }
warn()     { echo -e "${YELLOW}[deploy] WARNING:${RESET} $*" >&2; echo "$(_ts) WARN  $*" >> "${LOG_FILE:-/tmp/deploy.log}"; }
die()      { echo -e "${RED}[deploy] ERROR:${RESET} $*" >&2;   echo "$(_ts) ERROR $*" >> "${LOG_FILE:-/tmp/deploy.log}"; exit 1; }
step()     { echo -e "\n${BOLD}${CYAN}▶ $*${RESET}"; echo "$(_ts) STEP  $*" >> "${LOG_FILE:-/tmp/deploy.log}"; }
dim()      { echo -e "${DIM}$*${RESET}"; }

# ── defaults ──────────────────────────────────────────────────────────────
REPO_URL="${REPO_URL:-https://github.com/GrabowMar/AutoLLM_Code_Analyzer_rework.git}"
BRANCH="${BRANCH:-main}"
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/AutoLLM_Code_Analyzer_rework}"
COMPOSE_FILE="docker-compose.local.yml"
NO_PROXY="${NO_PROXY:-0}"
CI="${CI:-}"
OPENROUTER_KEY="${OPENROUTER_KEY:-}"
DOMAIN="${DOMAIN:-}"
CADDY_CONTAINER="${CADDY_CONTAINER:-}"

LOG_FILE="/tmp/deploy-autollm.log"   # temporary until DEPLOY_DIR exists
COMPOSE_CMD=()                        # resolved in check_prerequisites (bash array)

# ── helpers ───────────────────────────────────────────────────────────────

is_ci() { [[ "${CI:-}" == "1" || "${CI:-}" == "true" ]]; }

ask() {
  # ask <var_name> <prompt> [default]
  local var="$1" prompt="$2" default="${3:-}"
  if is_ci; then
    [[ -n "${!var:-}" ]] || die "CI mode: \$$var is required but not set."
    return
  fi
  local input
  if [[ -n "$default" ]]; then
    read -r -p "$(echo -e "${CYAN}?${RESET} ${prompt} [${default}]: ")" input
    printf -v "$var" '%s' "${input:-$default}"
  else
    while [[ -z "${!var:-}" ]]; do
      read -r -p "$(echo -e "${CYAN}?${RESET} ${prompt}: ")" input
      printf -v "$var" '%s' "$input"
    done
  fi
}

ask_secret() {
  # ask_secret <var_name> <prompt>
  local var="$1" prompt="$2"
  is_ci && return
  [[ -n "${!var:-}" ]] && return
  local input
  read -r -s -p "$(echo -e "${CYAN}?${RESET} ${prompt} (leave blank to skip): ")" input
  echo
  printf -v "$var" '%s' "$input"
}

docker_exec() {
  # Run docker compose with the correct prefix (plain or sudo)
  "${COMPOSE_CMD[@]}" "$@"
}

wait_healthy() {
  # wait_healthy <service> <url> <max_seconds>
  # Accepts any HTTP response (even 4xx) — we just need the service to be up.
  local svc="$1" url="$2" max="${3:-60}"
  info "Waiting for ${svc} to respond at ${url} …"
  local i=0
  until curl -s -o /dev/null -w '%{http_code}' "$url" 2>/dev/null | grep -qE '^[0-9]+$'; do
    ((i+=2))
    [[ $i -ge $max ]] && die "${svc} did not become healthy after ${max}s. Check: ${COMPOSE_CMD[*]} logs ${svc}"
    sleep 2
  done
  success "${svc} is healthy."
}

# ── step 1: prerequisites ─────────────────────────────────────────────────
check_prerequisites() {
  step "Checking prerequisites"

  local missing=()
  for cmd in git python3 curl; do
    command -v "$cmd" &>/dev/null || missing+=("$cmd")
  done
  [[ ${#missing[@]} -eq 0 ]] || die "Missing required tools: ${missing[*]}. Install them and re-run."

  # Resolve docker compose command into an array
  if docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose -f "${DEPLOY_DIR}/${COMPOSE_FILE}")
  elif sudo docker compose version &>/dev/null 2>&1; then
    COMPOSE_CMD=(sudo docker compose -f "${DEPLOY_DIR}/${COMPOSE_FILE}")
    warn "Running Docker with sudo. Consider adding your user to the docker group:"
    warn "  sudo usermod -aG docker \$USER  (then log out and back in)"
  else
    die "Docker Compose not found. Install Docker Engine + the Compose plugin:\n  https://docs.docker.com/engine/install/"
  fi

  # Validate docker daemon is reachable
  "${COMPOSE_CMD[0]}" info &>/dev/null \
    || die "Docker daemon is not running or not accessible."

  success "All prerequisites met."
  dim "  docker compose: $("${COMPOSE_CMD[0]}" compose version --short 2>/dev/null || echo 'unknown')"
}

# ── step 2: repo ──────────────────────────────────────────────────────────
setup_repo() {
  step "Repository"

  if [[ -d "${DEPLOY_DIR}/.git" ]]; then
    info "Updating existing repository at ${DEPLOY_DIR} …"
    git -C "${DEPLOY_DIR}" fetch --quiet origin
    git -C "${DEPLOY_DIR}" checkout --quiet "${BRANCH}"
    git -C "${DEPLOY_DIR}" reset --quiet --hard "origin/${BRANCH}"
    success "Repository updated to $(git -C "${DEPLOY_DIR}" rev-parse --short HEAD)."
  else
    info "Cloning ${REPO_URL} → ${DEPLOY_DIR} …"
    git clone --quiet --branch "${BRANCH}" "${REPO_URL}" "${DEPLOY_DIR}"
    success "Repository cloned."
  fi

  # Switch log to deploy dir now that it exists
  LOG_FILE="${DEPLOY_DIR}/deploy.log"
}

# ── step 3: environment files ─────────────────────────────────────────────
setup_environment() {
  step "Environment configuration"

  # Collect domain
  if [[ -z "$DOMAIN" ]]; then
    ask DOMAIN "Public domain / hostname for this deployment (e.g. myapp.example.com)"
  fi

  # Optional OpenRouter key
  if [[ -z "$OPENROUTER_KEY" ]] && ! is_ci; then
    ask_secret OPENROUTER_KEY "Global OpenRouter API key (https://openrouter.ai/keys)"
  fi

  # Run bootstrap.py (idempotent — won't overwrite existing files)
  info "Generating secret files …"
  umask 077
  python3 "${DEPLOY_DIR}/scripts/bootstrap.py" >> "${LOG_FILE}" 2>&1
  success "Secret files ready."

  # Patch .django env for this deployment
  local django_env="${DEPLOY_DIR}/.envs/.local/.django"

  _set_env() { local key="$1" val="$2"
    if grep -q "^${key}=" "${django_env}"; then
      sed -i "s|^${key}=.*|${key}=${val}|" "${django_env}"
    else
      echo "${key}=${val}" >> "${django_env}"
    fi
  }

  _set_env "DJANGO_DOMAIN"         "${DOMAIN}"
  _set_env "FRONTEND_PUBLIC_ORIGIN" "https://${DOMAIN}"
  _set_env "DJANGO_ALLOWED_HOSTS"  "localhost,127.0.0.1,django,${DOMAIN}"
  _set_env "CORS_ALLOWED_ORIGINS"  "https://${DOMAIN},http://localhost:8000"
  _set_env "CSRF_TRUSTED_ORIGINS"  "https://${DOMAIN},http://localhost:8000,http://localhost:8001"

  if [[ -n "$OPENROUTER_KEY" ]]; then
    _set_env "OPENROUTER_API_KEY" "${OPENROUTER_KEY}"
    _set_env "OPENROUTER_ALLOW_GLOBAL_KEY_FALLBACK" "True"
  fi

  # Lock down file permissions
  chmod 600 "${DEPLOY_DIR}/.envs/.local/.django" \
             "${DEPLOY_DIR}/.envs/.local/.postgres"

  success "Environment configured for ${DOMAIN}."
}

# ── step 4: docker group ──────────────────────────────────────────────────
ensure_docker_access() {
  # If we're using sudo, offer to add the user to the docker group once.
  [[ "${COMPOSE_CMD[0]}" != "sudo" ]] && return
  if groups "$USER" | grep -qw docker; then return; fi

  warn "User '${USER}' is not in the docker group."
  if is_ci; then
    info "CI mode: using sudo for all docker commands."
    return
  fi
  local ans
  read -r -p "$(echo -e "${CYAN}?${RESET} Add '${USER}' to the docker group now? [Y/n]: ")" ans
  ans="${ans:-Y}"
  if [[ "${ans,,}" == "y" ]]; then
    sudo usermod -aG docker "$USER"
    warn "Group change takes effect on next login. Continuing with sudo for this session."
  fi
}

# ── step 5: build & start ─────────────────────────────────────────────────
start_services() {
  step "Building and starting services"
  info "This may take several minutes on the first run …"

  docker_exec build --pull --quiet 2>> "${LOG_FILE}" \
    || { warn "Quiet build failed; retrying with output …"; docker_exec build --pull; }

  docker_exec up -d --remove-orphans
  success "All containers started."
}

# ── step 6: migrations ────────────────────────────────────────────────────
run_migrations() {
  step "Database migrations"
  docker_exec run --rm django python ./manage.py migrate --noinput
  success "Migrations applied."
}

# ── step 7: health checks ─────────────────────────────────────────────────
health_checks() {
  step "Health checks"

  # Give containers a moment to settle
  sleep 3

  # Django API
  wait_healthy "django"   "http://localhost:8001/"        60
  # SvelteKit frontend
  wait_healthy "frontend" "http://localhost:8000/"        90

  # Confirm all expected containers are running
  local unhealthy
  unhealthy=$(docker_exec ps --format json 2>/dev/null \
    | python3 -c "
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line: continue
    try:
        c = json.loads(line)
    except json.JSONDecodeError:
        continue
    state = c.get('State','').lower()
    health = c.get('Health','').lower()
    if state != 'running' or health in ('unhealthy',):
        print(c.get('Name','?') + ' -> ' + state + ('/' + health if health else ''))
" 2>/dev/null || true)

  if [[ -n "$unhealthy" ]]; then
    warn "Some containers may need attention:\n${unhealthy}"
  else
    success "All containers are running."
  fi
}

# ── step 8: reverse proxy ─────────────────────────────────────────────────
setup_reverse_proxy() {
  [[ "$NO_PROXY" == "1" ]] && { warn "Skipping reverse-proxy setup (NO_PROXY=1)."; return; }

  step "Reverse proxy"

  # ── Caddy ──────────────────────────────────────────────────────────────
  _try_caddy() {
    # Auto-detect a running Caddy container (plain docker, then sudo docker)
    local caddy_name="${CADDY_CONTAINER:-}"
    if [[ -z "$caddy_name" ]]; then
      caddy_name=$(docker ps --format '{{.Names}}' 2>/dev/null | grep -i caddy | head -1 || true)
      if [[ -z "$caddy_name" ]]; then
        caddy_name=$(sudo docker ps --format '{{.Names}}' 2>/dev/null | grep -i caddy | head -1 || true)
      fi
    fi
    [[ -z "$caddy_name" ]] && return 1

    # Find the Caddyfile bind-mount path on the host
    local fmt='{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}'
    local caddyfile
    caddyfile=$(docker inspect "$caddy_name" --format "$fmt" 2>/dev/null \
      || sudo docker inspect "$caddy_name" --format "$fmt" 2>/dev/null \
      || true)
    [[ -z "$caddyfile" || ! -f "$caddyfile" ]] && return 1

    # Already configured?
    if grep -q "${DOMAIN}" "$caddyfile" 2>/dev/null; then
      info "Domain ${DOMAIN} already present in ${caddyfile}."
    else
      # Resolve host IP reachable from inside the Caddy container
      local net_fmt='{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}'
      local caddy_net gw host_ip
      caddy_net=$(docker inspect "$caddy_name" --format "$net_fmt" 2>/dev/null \
        || sudo docker inspect "$caddy_name" --format "$net_fmt" 2>/dev/null \
        || true)
      caddy_net=$(echo "$caddy_net" | head -1)

      if [[ -n "$caddy_net" ]]; then
        gw=$(docker network inspect "$caddy_net" \
          --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null \
          || sudo docker network inspect "$caddy_net" \
          --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null \
          || true)
      fi
      host_ip="${CADDY_HOST_IP:-${gw:-host.docker.internal}}"

      info "Adding ${DOMAIN} → ${host_ip}:8000 to Caddyfile …"
      printf '\n%s {\n    reverse_proxy %s:8000\n}\n' "${DOMAIN}" "${host_ip}" \
        | tee -a "$caddyfile" >> "${LOG_FILE}"
    fi

    # Reload Caddy
    docker exec "$caddy_name" caddy reload --config /etc/caddy/Caddyfile 2>> "${LOG_FILE}" \
      || sudo docker exec "$caddy_name" caddy reload --config /etc/caddy/Caddyfile 2>> "${LOG_FILE}"

    success "Caddy configured — TLS certificate obtained automatically."
    PROXY_TYPE="caddy"
    return 0
  }

  # ── nginx ──────────────────────────────────────────────────────────────
  _try_nginx() {
    command -v nginx &>/dev/null || sudo nginx -v &>/dev/null 2>&1 || return 1

    local conf_file="/etc/nginx/sites-available/autollm"
    local link="/etc/nginx/sites-enabled/autollm"

    if [[ -f "$conf_file" ]] && grep -q "${DOMAIN}" "$conf_file" 2>/dev/null; then
      info "nginx already configured for ${DOMAIN}."
    else
      info "Writing nginx config for ${DOMAIN} …"
      local tmpfile; tmpfile=$(mktemp)
      # Write to tmpfile (unquoted heredoc: ${DOMAIN} expands, \$ becomes literal $)
      cat > "$tmpfile" <<NGINXCONF
server {
    listen 80;
    server_name ${DOMAIN};

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade \$http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host \$host;
        proxy_set_header   X-Real-IP \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }
}
NGINXCONF
      sudo cp "$tmpfile" "$conf_file"
      rm -f "$tmpfile"
      sudo ln -sf "$conf_file" "$link"
      sudo nginx -t && sudo systemctl reload nginx
    fi

    success "nginx configured. Add TLS with: sudo certbot --nginx -d ${DOMAIN}"
    PROXY_TYPE="nginx"
    return 0
  }

  PROXY_TYPE="none"
  _try_caddy || _try_nginx || {
    warn "No supported reverse proxy found (Caddy or nginx)."
    warn "The app is accessible on its raw ports (see summary below)."
  }
}

# ── step 9: print summary ─────────────────────────────────────────────────
print_summary() {
  step "Deployment complete"

  local proto="http"
  [[ "${PROXY_TYPE:-none}" =~ ^(caddy|nginx)$ ]] && proto="https"

  echo
  echo -e "${BOLD}${GREEN}✔ AutoLLM Code Analyzer is running${RESET}"
  echo
  if [[ "${PROXY_TYPE:-none}" == "caddy" ]]; then
    echo -e "  ${BOLD}Frontend   ${RESET}  ${GREEN}https://${DOMAIN}${RESET}  (TLS via Caddy)"
  elif [[ "${PROXY_TYPE:-none}" == "nginx" ]]; then
    echo -e "  ${BOLD}Frontend   ${RESET}  http://${DOMAIN}  (add TLS with certbot)"
  fi
  echo -e "  ${BOLD}Frontend   ${DIM}(direct)${RESET}  http://$(hostname -I | awk '{print $1}'):8000"
  echo -e "  ${BOLD}Django API ${DIM}(direct)${RESET}  http://$(hostname -I | awk '{print $1}'):8001"
  echo -e "  ${BOLD}Admin      ${DIM}(direct)${RESET}  http://$(hostname -I | awk '{print $1}'):8001/admin/"
  echo -e "  ${BOLD}Mailpit    ${DIM}(direct)${RESET}  http://$(hostname -I | awk '{print $1}'):8025"
  echo -e "  ${BOLD}Flower     ${DIM}(direct)${RESET}  http://$(hostname -I | awk '{print $1}'):5555"
  echo
  echo -e "  ${BOLD}Log file${RESET}  ${LOG_FILE}"
  echo
  echo -e "${CYAN}Next steps:${RESET}"
  echo -e "  Create a superuser:"
  echo -e "  ${DIM}cd ${DEPLOY_DIR} && docker compose -f ${DEPLOY_DIR}/${COMPOSE_FILE} run --rm django python ./manage.py createsuperuser${RESET}"
  echo
}

# ── orchestrate ───────────────────────────────────────────────────────────
main() {
  echo -e "${BOLD}${CYAN}"
  echo "  ╔══════════════════════════════════════════╗"
  echo "  ║   AutoLLM Code Analyzer — deploy.sh      ║"
  echo "  ╚══════════════════════════════════════════╝"
  echo -e "${RESET}"

  check_prerequisites
  setup_repo
  LOG_FILE="${DEPLOY_DIR}/deploy.log"  # switch to permanent log
  setup_environment
  ensure_docker_access
  start_services
  run_migrations
  health_checks
  setup_reverse_proxy
  print_summary
}

main "$@"
