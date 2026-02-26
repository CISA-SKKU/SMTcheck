# L1 Data Cache Analysis

This document presents the reverse-engineering results for the L1 data cache.

<table>
  <tr>
    <td><img src="./plots/wo_smt.png" width="100%"></td>
    <td><img src="./plots/w_smt.png" width="100%"></td>
  </tr>
</table>

## Observations

- As the stride increases, the address bits used for set indexing shift to higher positions. When the stride exceeds the set index range, all accesses map to the same set, causing the performance curves to converge.
- In these results, curves converge at stride 2^12, so the MSB of the set index is at bit position **11** (0-indexed).
- Since the cache block size is 64 bytes, we exclude the 6-bit block offset, leaving **6 bits** for the set index.
- Therefore, the L1 data cache has **64 sets**.
- Cache misses begin to increase when the number of ways exceeds 12, indicating **12-way** associativity.
- Since the results are similar with and without SMT, the L1 data cache uses **competitive sharing**.

---

**NOTE**
- While IPC can be used for reverse-engineering, hardware-specific events can remove the impact of other resources.
- Metric 2 uses L1_DCACHE_MISS events and shows results similar to the IPC drop pattern.