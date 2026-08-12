"""Source-checkout compatibility import; installed users use the package API."""

from bili_fact_checker.api.app import create_app, main

app = create_app()

__all__ = ["app", "main"]


if __name__ == "__main__":
    main()
