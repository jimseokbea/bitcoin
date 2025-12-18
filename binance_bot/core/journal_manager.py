import csv
import os
from datetime import datetime
from .system_utils import LOGGER

class JournalManager:
    def __init__(self, filename="trade_history.csv"):
        self.filename = filename
        
    def log_trade(self, trade_data):
        """
        Appends a trade record to the CSV file.
        expected keys in trade_data: 
        ['EntryTime', 'ExitTime', 'Symbol', 'Side', 'EntryPrice', 'ExitPrice', 'PnL', 'PnL%', 'ADX', 'Volatility', 'Notes']
        """
        try:
            file_exists = os.path.isfile(self.filename)
            
            # Ensure all standard keys exist
            if 'ExitTime' not in trade_data: trade_data['ExitTime'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            with open(self.filename, 'a', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=trade_data.keys())
                
                if not file_exists:
                    writer.writeheader()
                    
                writer.writerow(trade_data)
                LOGGER.info(f"💾 매매 일지 저장 완료: {self.filename}")
                
        except Exception as e:
            LOGGER.error(f"매매 일지 저장 실패: {e}")
