import { Component, OnInit, inject, signal, PLATFORM_ID } from '@angular/core';
import { isPlatformBrowser, CommonModule, DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { MarketSnapshotService, MarketSnapshot, MarketTopicSnapshot } from '../services/market-snapshot.service';

@Component({
  selector: 'app-home',
  imports: [CommonModule, DatePipe],
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

  topics = signal<MarketTopicSnapshot[]>([]);
  topicsLoading = signal(true);
  topicsError = signal(false);
  topicsUpdatedAt = signal<string | null>(null);

  ngOnInit(): void {
    this.dateStr.set(new Date().toLocaleDateString('zh-TW'));
    if (isPlatformBrowser(this.platformId)) {
      this.loadSnapshot();
      this.loadTopics();
    }
  }

  /**
   * 取最新一筆快照：每日快照於收盤後 17:30 產生（21:30 補齊盤後資料），
   * 因此盤中顯示的是上一個交易日的資料。
   */
  private loadSnapshot(): void {
    this.isLoading.set(true);
    this.hasError.set(false);

    this.marketSnapshotService.getLatest().subscribe({
      next: (data) => {
        this.snapshot.set(data);
        this.isLoading.set(false);
      },
      error: (err: HttpErrorResponse) => {
        // 404 = 尚無任何快照資料，顯示「無資料」而非錯誤
        if (err.status === 404) {
          this.snapshot.set(null);
        } else {
          console.error('[MarketSnapshot] API error:', err);
          this.hasError.set(true);
        }
        this.isLoading.set(false);
      },
    });
  }

  loadTopics(): void {
    this.topicsLoading.set(true);
    this.topicsError.set(false);

    this.marketSnapshotService.getLatestTopics().subscribe({
      next: (data) => {
        this.topics.set(data);
        this.topicsUpdatedAt.set(new Date().toLocaleTimeString('zh-TW'));
        this.topicsLoading.set(false);
      },
      error: (err: HttpErrorResponse) => {
        if (err.status === 404) {
          this.topics.set([]);
        } else {
          console.error('[MarketTopics] API error:', err);
          this.topicsError.set(true);
        }
        this.topicsLoading.set(false);
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
