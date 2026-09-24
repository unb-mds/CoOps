import { describe, test, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import DataNotGenerated, { DataNotConfigured } from './DataNotGenerated';

describe('DataNotGenerated', () => {
  test('explica que o arquivo ainda não foi gerado e nomeia o arquivo', () => {
    render(<DataNotGenerated path="silver/temporal_events.json" />);
    const status = screen.getByRole('status');
    expect(status).toHaveTextContent("This data hasn't been generated yet");
    expect(status).toHaveTextContent('data/silver/temporal_events.json');
    expect(status).toHaveTextContent(/daily data pipeline creates it/);
    expect(status).toHaveTextContent(/Bronze Layer - Data Extraction/);
    expect(screen.queryByRole('alert')).toBeNull();
  });

  test('mostra a dica extra quando informada', () => {
    render(<DataNotGenerated path="silver/x.json" hint="Needs a secret." />);
    expect(screen.getByRole('status')).toHaveTextContent('Needs a secret.');
  });

  test('aceita classes adicionais', () => {
    render(<DataNotGenerated path="silver/x.json" className="mt-4" />);
    expect(screen.getByTestId('data-not-generated')).toHaveClass('mt-4');
  });
});

describe('DataNotConfigured', () => {
  test('instrui a configurar VITE_GITHUB_ORG', () => {
    render(<DataNotConfigured />);
    const status = screen.getByTestId('data-not-configured');
    expect(status).toHaveTextContent('VITE_GITHUB_ORG is not configured');
    expect(status).toHaveTextContent('VITE_GITHUB_ORG');
  });

  test('aceita classes adicionais', () => {
    render(<DataNotConfigured className="mt-4" />);
    expect(screen.getByTestId('data-not-configured')).toHaveClass('mt-4');
  });
});
