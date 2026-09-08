# ===== BOT SETTINGS =====
# Your bot's name and race (use plain strings)
BOT_NAME = "KauKauBot"
BOT_RACE = "Zerg"  # Options: Terran, Protoss, Zerg, Random

# ===== GAME SETTINGS =====
# Maps configuration
# Set to None to use default SC2 maps, or specify the full path to the Maps directory
# Examples (include the Maps folder in the path):
#   MAP_PATH = "C:/Program Files (x86)/StarCraft II/Maps"  # Standard Windows path
#   MAP_PATH = "/Applications/StarCraft II/Maps"          # Mac
#   MAP_PATH = "~/StarCraftII/Maps"                      # Linux
# Specifying the full path to Maps directory helps avoid case sensitivity issues
MAP_PATH = "C:/Program Files (x86)/StarCraft II/Maps"  # Default Windows path - modify as needed

# List of maps to play on (randomly selected if not specified)
MAP_POOL = [
    "PersephoneAIE_v4",
    "PylonAIE_v4",
    "TorchesAIE_v4"
]

# ===== OPPONENT SETTINGS =====
# Computer opponent settings (for local games)
OPPONENT_RACE = "Terran"  # Terran, Zerg, Protoss, Random
# Max non-cheat difficulty (CheatVision/CheatMoney/CheatInsane also exist)
OPPONENT_DIFFICULTY = "VeryHard"

# ===== GAME MODE =====
# Set to True to play in realtime (like a human), False for faster simulation
REALTIME = False

# ===== LOCAL DEBUG =====
# Early-end local games on enemy GG chat OR computer wipe/surrender
# (uses debug DeclareVictory - not a resign).
# Ladder zips omit config.py, so this stays OFF on the ladder automatically.
LEAVE_ON_GG = True

# ===== BUILD SELECT =====
# Local only (omitted from ladder zip). Options: ling_rush, upgrade_rush, or None for random
FORCE_BUILD = "ling_rush"




