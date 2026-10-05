#!/usr/bin/env python3

import subprocess
import concurrent.futures
import os
import sys
import argparse
import time

def run_command(command, dry_run=False):
    """Executes a single command string in the shell."""
    if dry_run:
        return True, f"Dry-run: {command}"
    try:
        # We use shell=True because the commands in the .run file include 'cd', ';' and '>'
        result = subprocess.run(command, shell=True, capture_output=True, text=True)
        if result.returncode != 0:
            return False, f"Error executing command: {command}\nStderr: {result.stderr}"
        return True, None
    except Exception as e:
        return False, str(e)

def main():
    parser = argparse.ArgumentParser(description="Run tasks from a .run file in parallel.")
    parser.add_argument("run_file", help="Path to the .run file containing commands.")
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count(), 
                        help="Number of parallel jobs (default: number of CPU cores).")
    parser.add_argument("--dry-run", action="store_true", 
                        help="Show commands without executing them.")
    
    args = parser.parse_args()

    if not os.path.exists(args.run_file):
        print(f"Error: File {args.run_file} not found.")
        sys.exit(1)

    with open(args.run_file, 'r') as f:
        commands = [line.strip() for line in f if line.strip()]

    total_tasks = len(commands)
    if total_tasks == 0:
        print("No tasks found in the file.")
        return

    print(f"Total tasks to run: {total_tasks}")
    if args.dry_run:
        print("DRY RUN mode - commands will not be executed.")
    else:
        print(f"Running with {args.jobs} parallel jobs...")

    start_time = time.time()
    completed_tasks = 0
    failed_tasks = []

    # If dry run, we don't really need a process pool but we can use it anyway
    # or just loop through. For simplicity and to test the logic, we use the pool.
    max_workers = 1 if args.dry_run else args.jobs
    
    with concurrent.futures.ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_command = {executor.submit(run_command, cmd, args.dry_run): cmd for cmd in commands}
        
        for future in concurrent.futures.as_completed(future_to_command):
            cmd = future_to_command[future]
            success, result_msg = future.result()
            completed_tasks += 1
            
            if success:
                if args.dry_run:
                    print(f"[{completed_tasks}/{total_tasks}] {result_msg}")
                else:
                    print(f"[{completed_tasks}/{total_tasks}] Task completed successfully.")
            else:
                print(f"[{completed_tasks}/{total_tasks}] Task FAILED.")
                failed_tasks.append((cmd, result_msg))

    end_time = time.time()
    duration = end_time - start_time

    print("\n" + "="*30)
    if not args.dry_run:
        print(f"Finished in {duration:.2f} seconds.")
        print(f"Successfully completed: {completed_tasks - len(failed_tasks)}/{total_tasks}")
        
        if failed_tasks:
            print(f"Failed tasks: {len(failed_tasks)}")
            with open("failed_tasks.log", "w") as f:
                for cmd, error in failed_tasks:
                    f.write(f"COMMAND: {cmd}\nERROR: {error}\n{'-'*20}\n")
            print("Details of failed tasks have been written to failed_tasks.log")
    else:
        print(f"Dry run finished. {total_tasks} tasks parsed.")

if __name__ == "__main__":
    main()
