
import pandas as pd
import yaml
from core.strategy import HybridStrategy
import traceback

with open('config.yaml', encoding='utf-8') as f:
    config = yaml.safe_load(f)

print("Config loaded.")
s = HybridStrategy(config)
print("Strategy init done.")

# Create dummy DF
data = {
    'open': [100]*60,
    'high': [101]*60,
    'low': [99]*60,
    'close': [100]*60,
    'volume': [1000]*60
}
df = pd.DataFrame(data)
# Add required cols for verify
try:
    s.analyze(df)
    print("Analyze done.")
except Exception:
    traceback.print_exc()
