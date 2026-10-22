# config.py - ustawienia monitora (bez hasel: te sa w secrets.py)

# --- piny ESP32-C3 SuperMini -> wyswietlacz ---
# omijamy GPIO 2, 8, 9 (piny startowe C3; 8 to tez dioda na plytce)
PIN_SCK = 4  # SCK
PIN_MOSI = 6  # SDA
PIN_MISO = 5  # nieuzywany, ale SPI chce jakis pin - inaczej wzialby GPIO 2
PIN_CS = 7  # CS
PIN_DC = 3  # A0
PIN_RST = 10  # RESET
PIN_LED = 1  # LED (podswietlenie, PWM); None = LED wpiety na stale w 3V3
SPI_BAUDRATE = 26_000_000

# --- wariant modulu (sprawdz test.py) ---
# kolory zamienione (czerwony zamiast niebieskiego) -> TFT_BGR = True
TFT_BGR = False
# obraz do gory nogami -> zmien TFT_FLIP
TFT_FLIP = True
# paski smieci na krawedzi -> przesuniecie, zwykle (2, 1) albo (0, 0)
TFT_X_OFFSET = 0
TFT_Y_OFFSET = 0
# negatyw -> True
TFT_INVERT = False

# --- glowne ESP GrowBoxa ---
ESP_HOST = "192.168.18.100"
# nazwa z DHCP glownego ESP; proba, gdy adres przestanie odpowiadac
ESP_MDNS = "esp32.local"

# co ile pytac (s). Glowne ESP obsluguje jedno zapytanie naraz i ma malo
# RAM-u, a pytaja go tez aplikacja i widget - stad rzadziej niz widget (10 s).
POLL_CONFIG_S = 15
# log klimatu ESP zapisuje co 5 min
POLL_HISTORY_S = 300
HTTP_TIMEOUT_S = 8
# po tylu nieudanych zapytaniach szukamy ESP pod innym adresem
FAILURES_BEFORE_SEARCH = 3

# --- podswietlenie (0-100 %) ---
BACKLIGHT_DAY = 100
BACKLIGHT_NIGHT = 15
# noc wg zegara (godziny lokalne)
NIGHT_FROM = 22
NIGHT_TO = 7
