import time, glob, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("Loading engine...")
from core.bank_reconcile_engine import reconcile_files

runs = sorted(glob.glob("d:/audit_files/runs/*账款相符*"), key=os.path.getmtime, reverse=True)
rundir = runs[0]
inputs_dir = rundir + "/inputs"
book = [f for f in os.listdir(inputs_dir) if "序时账" in f][0]
bank = [f for f in os.listdir(inputs_dir) if "银行流水" in f][0]

print(f"Files: {book}, {bank}")
t0 = time.time()
r = reconcile_files(inputs_dir + "/" + book, inputs_dir + "/" + bank)
t = time.time() - t0

s = r["stats"]
print(f"Time: {t:.1f}s")
print(f"L1:{s['matched_L1']} L2:{s['matched_L2']} L3:{s['matched_L3_groups']} L4:{s['review_L4']}")
print(f"Book:{s['book_match_rate']}% Bank:{s['bank_match_rate']}%")
print(f"Red flags:{s['red_flag_count']}")
print("OK")