"""
demo_heavy.py — simulated ML workload for testing the grid scheduling feature.
Open this file in VS Code with the extension running to see the amber
'🌱 run greener' decoration and hover tooltip on line 1.
"""

import numpy as np
import pandas as pd

# Load and preprocess data
data = pd.read_csv("emissions_dataset.csv")
X = np.array(data["Energy_Consumed_kWh"].tolist())
y = np.array(data["CO2_Emissions_g"].tolist())

# Matrix operations
cov = np.dot(X.reshape(-1, 1), X.reshape(1, -1))
result = np.matmul(cov[:10, :10], cov[:10, :10])

# Nested loops (intentional — triggers heavy scoring)
totals = []
for i in range(len(data)):
    row_sum = 0
    for j in range(5):
        row_sum += float(data.iloc[i]["Energy_Consumed_kWh"]) * j
    totals.append(row_sum)

print("Heavy workload demo complete.")
