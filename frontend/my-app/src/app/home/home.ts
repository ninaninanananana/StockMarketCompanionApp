import { Component, OnInit, inject, signal, PLATFORM_ID } from '@angular/core';
import { isPlatformBrowser, CommonModule } from '@angular/common';
import { MarketSnapshotService, MarketSnapshot } from '../services/market-snapshot.service';

@Component({
  selector: 'app-home',
  imports: [CommonModule],
  templateUrl: './home.html',
  styleUrl: './home.scss',
})
export class Home implements OnInit {
  private readonly marketSnapshotService = inject(MarketSnapshotService);
  private readonly platformId = inject(PLATFORM_ID);

  dateStr = signal('');
  snapshot = signal<MarketSnapshot | null>(null);
  isLoading = signal(true);
  hasError = signal(false);

  ngOnInit(): void {
    this.dateStr.set(new Date().toLocaleDateString('zh-TW'));
    if (isPlatformBrowser(this.platformId)) {
      this.loadSnapshot();
    }
  }

  private getYesterday(): string {
    const d = new Date();
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10); // YYYY-MM-DD
  }

  private loadSnapshot(): void {
    this.isLoading.set(true);
    this.hasError.set(false);

    this.marketSnapshotService.getByDate(this.getYesterday()).subscribe({
      next: (data) => {
        // API 無資料時回 []，有資料時回物件
        this.snapshot.set(Array.isArray(data) ? null : data);
        console.log(this.snapshot())
        this.isLoading.set(false);
      },
      error: (err) => {
        console.error('[MarketSnapshot] API error:', err);
        this.hasError.set(true);
        this.isLoading.set(false);
      },
    });
  }

  /** 將億元單位的法人買賣超格式化，e.g. 1234567890 → "+12.3 億" */
  formatYi(val: number): string {
    if (val === 0) return '—';
    const yi = val / 1_0000_0000;
    return (yi > 0 ? '+' : '') + yi.toFixed(1) + ' 億';
  }

  /** market_score → 圓環 stroke-dashoffset（circumference = 2π×34 ≈ 213.6） */
  get ringOffset(): number {
    const score = this.snapshot()?.market_score ?? 0;
    return 213.6 * (1 - score / 100);
  }
}
