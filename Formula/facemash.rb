class facemash < Formula
  desc "Menu-bar app that nudges you when you touch your face, for better skin"
  homepage "https://github.com/Josh-Ruben/facemash"
  url "https://github.com/Josh-Ruben/facemash/releases/download/v0.3.0/facemash-0.3.0.tar.gz"
  sha256 "64cae65dc0f00c8694ab24967f33c25be021647a15a6b8c4c927f775841ef760"
  license "MIT"

  # MediaPipe currently publishes wheels for Python 3.9-3.12, so pin 3.12.
  depends_on "python@3.12"

  def install
    # MediaPipe + OpenCV pull a large transitive dependency tree that is only
    # distributed as wheels, so we build an isolated virtualenv and let pip
    # resolve everything from PyPI rather than vendoring dozens of resources.
    venv = libexec
    system Formula["python@3.12"].opt_bin/"python3.12", "-m", "venv", venv
    system venv/"bin/pip", "install", "--upgrade", "pip", "wheel"
    system venv/"bin/pip", "install", buildpath
    bin.install_symlink venv/"bin/facemash"
  end

  def caveats
    <<~EOS
      facemash runs from your menu bar. Start it with:
        facemash

      On first launch macOS will ask for Camera permission. Approve it under
      System Settings -> Privacy & Security -> Camera, then relaunch.

      Everything runs locally; no video ever leaves your Mac. It also downloads
      two small MediaPipe model files (~8 MB) into
        ~/Library/Application Support/facelint/models
      the first time it runs.
    EOS
  end

  test do
    system libexec/"bin/python", "-c", "import facemash; assert facemash.__version__"
  end
end
