{{task}}

You can execute bash commands to read and search the code. This is a review:
do not edit the application's files.

## Recommended Workflow

This workflow should be done step-by-step so that you can build up an understanding of the code.

1. List the files and read the files under review (use line numbers, e.g. `nl -ba file | sed -n '1,200p'`)
2. Search the code for related definitions and usages (`grep -rn`)
3. Note each problem with its exact line numbers and evidence lines
4. Write the findings JSON document to `@@findings_path@@`, for example with a quoted heredoc (`cat <<'EOF' > @@findings_path@@`)
5. Submit and finish your work by issuing the following command: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat @@findings_path@@`.
   Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

## Command Execution Rules

You are operating in an environment where

1. You issue at least one command
2. The system executes the command(s) in a subshell
3. You see the result(s)
4. You write your next command(s)

Each response should include:

1. **Reasoning text** where you explain your analysis and plan
2. At least one tool call with your command

**CRITICAL REQUIREMENTS:**

- Your response SHOULD include reasoning text explaining what you're doing
- Your response MUST include AT LEAST ONE bash tool call
- Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
- However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files
- Submit and finish your work by issuing the following command: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat @@findings_path@@`.
  Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

Example of a CORRECT response:
<example_response>
I need to understand the structure of the repository first. Let me check what files are in the current directory to get a better understanding of the codebase.

[Makes bash tool call with {"command": "ls -la"} as arguments]
</example_response>

<system_information>
{{system}} {{release}} {{version}} {{machine}}
</system_information>

## Useful command examples

### View file content:

```bash
# View specific lines with numbers
nl -ba filename.py | sed -n '10,20p'
```

### Search the code:

```bash
grep -rn "pattern" --include='*.py' .
```

### Any other command you want to run

```bash
anything
```
