import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'blotquant',
  description:
    'QC-first western blot densitometry. Every number carries its QC flags, its ROI and the ' +
    'parameter set it was measured under.',
};

/** The root layout. Wave 2 fills in the shell; this establishes the register and the fonts. */
export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>): React.ReactElement {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
