import os
import importlib
import sys
import json

def main():
    """
    Main entry point for the python-executor.
    Dispatches to the module specified by the TASK_MODULE environment variable.
    """
    task_module_name = os.environ.get("TASK_MODULE")
    task_args_json = os.environ.get("TASK_ARGS", "{}")

    if not task_module_name:
        print("Error: TASK_MODULE environment variable is not set.")
        sys.exit(1)

    try:
        task_args = json.loads(task_args_json)
    except json.JSONDecodeError as e:
        print(f"Error: Failed to parse TASK_ARGS as JSON: {e}")
        sys.exit(1)

    print(f"Loading task module: {task_module_name}")
    try:
        # Task module can be a full path like 'tasks.my_task' or 'skills.my_skill.scripts.run'
        module = importlib.import_module(task_module_name)
    except ImportError as e:
        print(f"Error: Could not import module {task_module_name}: {e}")
        sys.exit(1)

    if hasattr(module, "run"):
        print(f"Executing {task_module_name}.run()")
        module.run(**task_args)
    elif hasattr(module, "main"):
        print(f"Executing {task_module_name}.main()")
        module.main(**task_args)
    else:
        print(f"Error: Module {task_module_name} does not have a run() or main() function.")
        sys.exit(1)

if __name__ == "__main__":
    main()
