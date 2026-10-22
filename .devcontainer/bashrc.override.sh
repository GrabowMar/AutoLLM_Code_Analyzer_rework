
#
# .bashrc.override.sh
#

# persistent bash history
HISTFILE=~/.bash_history
PROMPT_COMMAND="history -a; $PROMPT_COMMAND"

# set some django env vars
source /entrypoint

# restore default shell options
set +o errexit
set +o pipefail
set +o nounset

# start ssh-agent
# https://code.visualstudio.com/docs/remote/troubleshooting
eval "$(ssh-agent -s)"

# login shells re-prepend the image's root-owned /app/.venv; prefer dev-user's venv
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    PATH="${VIRTUAL_ENV}/bin:${PATH}"
fi
