import platform

if platform.system() == "Windows":
    from facemash.windows_app import main
else:
    from facemash.app import main


if __name__ == "__main__":
    main()
