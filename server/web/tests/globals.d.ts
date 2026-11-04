// Testler Playwright altında Node'da koşar ama @types/node kurulu değil. Yalnızca kullandığımız
// parçalar burada bildirilir; uygulama kodu (src) bunları kullanmaz.
declare class Buffer {
  static from(data: string, encoding?: string): Buffer;
  static from(data: ArrayBuffer | ArrayLike<number>): Buffer;
  static alloc(size: number, fill?: number | string): Buffer;
  static concat(list: Buffer[]): Buffer;
  readonly length: number;
  writeUInt32LE(value: number, offset: number): number;
  writeUInt16LE(value: number, offset: number): number;
  writeInt16LE(value: number, offset: number): number;
  write(text: string, offset: number): number;
}

declare const process: { env: Record<string, string | undefined> };
