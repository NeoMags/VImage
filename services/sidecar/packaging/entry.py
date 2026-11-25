import multiprocessing
import sys

if __name__ == "__main__":
    # Job-queue workers are spawned processes; in a frozen build they re-run this exe and
    # freeze_support() turns them into workers instead of starting a second engine.
    multiprocessing.freeze_support()
    from ivsidecar.__main__ import main

    sys.exit(main())
