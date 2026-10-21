import ctypes
import subprocess
import sys
from pathlib import Path

# =====================================
# 1. 필요한 라이브러리 자동 설치
# =====================================
def install_package(package_name):
    try:
        __import__("PIL")
        print("Pillow가 이미 설치되어 있습니다.")
    except ImportError:
        print("Pillow가 없습니다. 자동 설치를 시작합니다...")

        result = subprocess.run([
            sys.executable,
            "-m", "pip",
            "install", package_name
        ])

        if result.returncode != 0:
            print("Pillow 설치 실패!")
            print("인터넷 연결 및 pip 설정을 확인하세요.")
            input("Enter 키를 누르면 종료합니다...")
            sys.exit(1)

        print("Pillow 설치 완료!")


install_package("Pillow")

# Pillow 설치 후 가져오기
from PIL import Image, ImageDraw, ImageFont

# =====================================
# 2. 사용자 설정
# =====================================
PC_NAME = "voiceorb-pc-1"
FONT_SIZE = 200
FONT_COLOR = (255, 255, 255)
BACKGROUND_COLOR = (0, 0, 0)

# right-bottom / center / left-top
POSITION = "center"

# =====================================
# 3. 바탕화면 경로
# =====================================
home = Path.home()

desktop_candidates = [
    home / "Desktop",
    home / "OneDrive" / "Desktop",
]

desktop = next(
    (p for p in desktop_candidates if p.exists()),
    home / "Desktop"
)

desktop.mkdir(parents=True, exist_ok=True)

output = desktop / "MY-PC-001_wallpaper.bmp"

# =====================================
# 4. 화면 해상도 확인
# =====================================
user32 = ctypes.windll.user32
width = user32.GetSystemMetrics(0)
height = user32.GetSystemMetrics(1)

# =====================================
# 5. 검은색 배경 생성
# =====================================
image = Image.new(
    "RGB",
    (width, height),
    BACKGROUND_COLOR
)

draw = ImageDraw.Draw(image)

# =====================================
# 6. 글꼴 설정
# =====================================
font_path = r"C:\Windows\Fonts\segoeui.ttf"

try:
    font = ImageFont.truetype(font_path, FONT_SIZE)
except OSError:
    font = ImageFont.load_default()

# =====================================
# 7. PC 이름 위치 계산
# =====================================
bbox = draw.textbbox((0, 0), PC_NAME, font=font)

text_width = bbox[2] - bbox[0]
text_height = bbox[3] - bbox[1]

margin = 40

if POSITION == "center":
    x = (width - text_width) // 2
    y = (height - text_height) // 2

elif POSITION == "left-top":
    x = margin
    y = margin

else:
    x = width - text_width - margin
    y = height - text_height - margin

# =====================================
# 8. PC 이름 표시
# =====================================
draw.text(
    (x, y),
    PC_NAME,
    font=font,
    fill=FONT_COLOR
)

# =====================================
# 9. 이미지 저장
# =====================================
image.save(output, "BMP")

print(f"배경 이미지 생성 완료: {output}")

# =====================================
# 10. Windows 배경화면 적용
# =====================================
SPI_SETDESKWALLPAPER = 20
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

result = user32.SystemParametersInfoW(
    SPI_SETDESKWALLPAPER,
    0,
    str(output),
    SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
)

if result:
    print("Windows 바탕화면 적용 성공!")
    print(f"PC 이름: {PC_NAME}")
else:
    print("이미지는 생성했지만 배경화면 적용에 실패했습니다.")

input("Enter 키를 누르면 종료합니다...")