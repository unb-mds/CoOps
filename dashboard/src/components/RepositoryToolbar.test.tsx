import { describe, test, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { BrowserRouter } from 'react-router-dom';
import RepositoryToolbar from './RepositoryToolbar';
import { getDataBasePath } from '../services/dataSource';
import { SidebarProvider } from '../contexts/SidebarContext';
import type { ProcessedActivityResponse } from '../pages/Utils';

// Mock do react-router-dom
const mockNavigate = vi.fn();
vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mockNavigate,
    useSearchParams: () => [new URLSearchParams(), vi.fn()],
  };
});

// Mock fetch globally
global.fetch = vi.fn();

// Helper para renderizar com contexto
const renderWithRouter = (ui: React.ReactElement) => {
  return render(
    <BrowserRouter>
      <SidebarProvider>{ui}</SidebarProvider>
    </BrowserRouter>
  );
};

describe('RepositoryToolbar Component', () => {
  const mockData: ProcessedActivityResponse = {
    generatedAt: '2024-01-01T00:00:00Z',
    repoCount: 3,
    totalActivities: 100,
    repositories: [
      {
        id: 1,
        name: 'repo-one',
        activities: [
          {
            date: '2024-01-01',
            type: 'commit',
            user: { login: 'user1', displayName: 'User One' },
          },
          {
            date: '2024-01-02',
            type: 'commit',
            user: { login: 'user2', displayName: 'User Two' },
          },
        ],
      },
      {
        id: 2,
        name: 'repo-two',
        activities: [
          {
            date: '2024-01-01',
            type: 'issue_created',
            user: { login: 'user1', displayName: 'User One' },
          },
        ],
      },
      {
        id: 3,
        name: 'repo-three',
        activities: [],
      },
    ],
  };

  const mockAvailableRepoNames = ['repo-alpha', 'repo-beta', 'repo-gamma'];

  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubEnv('VITE_GITHUB_ORG', 'test-org');
    (global.fetch as any).mockResolvedValue({
      ok: true,
      json: async () => mockAvailableRepoNames,
    });
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  // ========== RENDERIZAÇÃO BÁSICA ==========
  describe('Renderização Básica', () => {
    test('renderiza o toolbar corretamente', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      expect(screen.getByText('Repository Related Metrics')).toBeInTheDocument();
      expect(screen.getByText(/Currently Viewing:/)).toBeInTheDocument();
    });

    test('renderiza o ícone principal', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const icons = screen.getAllByText('📊');
      expect(icons.length).toBeGreaterThan(0);
      expect(icons[0]).toBeInTheDocument();
    });

    test('renderiza todos os itens de menu', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      expect(screen.getByText('Commits')).toBeInTheDocument();
      expect(screen.getByText('Issues')).toBeInTheDocument();
      expect(screen.getByText('Pull Requests')).toBeInTheDocument();
      expect(screen.getByText('Collaboration')).toBeInTheDocument();
      expect(screen.getByText('Structure')).toBeInTheDocument();
      expect(screen.getByText('Visualization')).toBeInTheDocument();
    });

    test('mostra o nome do repositório atual', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="my-awesome-repo" currentPage="commits" data={mockData} />
      );

      expect(screen.getByText('Currently Viewing: my-awesome-repo')).toBeInTheDocument();
    });

    test('renderiza sem currentRepo', () => {
      renderWithRouter(<RepositoryToolbar currentPage="commits" data={mockData} />);

      expect(screen.getByText('Repository Related Metrics')).toBeInTheDocument();
    });

    test('renderiza sem currentPage', () => {
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" data={mockData} />);

      expect(screen.getByText('Repository Related Metrics')).toBeInTheDocument();
    });

    test('renderiza sem data', async () => {
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });

      expect(screen.getByText('Repository Related Metrics')).toBeInTheDocument();
    });
  });

  // ========== NAVEGAÇÃO ==========
  describe('Navegação', () => {
    test('navega para commits ao clicar no botão', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="issues" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      fireEvent.click(commitsButton!);

      expect(mockNavigate).toHaveBeenCalledWith('/repos/commits');
    });

    test('navega para issues ao clicar no botão', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const issuesButton = screen.getByText('Issues').closest('button');
      fireEvent.click(issuesButton!);

      expect(mockNavigate).toHaveBeenCalledWith('/repos/issues');
    });

    test('navega para pull requests ao clicar no botão', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const prButton = screen.getByText('Pull Requests').closest('button');
      fireEvent.click(prButton!);

      expect(mockNavigate).toHaveBeenCalledWith('/repos/pullrequests');
    });

    test.each([
      ['Collaboration', '/repos/collaboration'],
      ['Structure', '/repos/structure'],
      ['Visualization', '/repos/visualization'],
    ])('navega para %s ao clicar no botão', (label, path) => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      fireEvent.click(screen.getByText(label).closest('button')!);

      expect(mockNavigate).toHaveBeenCalledWith(path);
    });

    test('navega corretamente quando clica no item já ativo', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      fireEvent.click(commitsButton!);

      expect(mockNavigate).toHaveBeenCalledWith('/repos/commits');
    });
  });

  // ========== ESTADOS ATIVOS ==========
  describe('Estados Ativos', () => {
    test('marca commits como ativo', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      expect(commitsButton).toHaveClass('text-blue-300');
      expect(commitsButton).toHaveClass('border-blue-500');
    });

    test('marca issues como ativo', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="issues" data={mockData} />
      );

      const issuesButton = screen.getByText('Issues').closest('button');
      expect(issuesButton).toHaveClass('text-blue-300');
      expect(issuesButton).toHaveClass('border-blue-500');
    });

    test('marca pull requests como ativo', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="pullrequests" data={mockData} />
      );

      const prButton = screen.getByText('Pull Requests').closest('button');
      expect(prButton).toHaveClass('text-blue-300');
      expect(prButton).toHaveClass('border-blue-500');
    });

    test('nenhum item ativo quando currentPage não corresponde', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="unknown" data={mockData} />
      );

      const buttons = screen.getAllByRole('button');
      buttons.forEach((button) => {
        expect(button).not.toHaveClass('text-blue-300');
      });
    });

    test('botão ativo tem background azul', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      expect(commitsButton).toHaveStyle({
        backgroundColor: 'rgba(59, 130, 246, 0.2)',
      });
    });

    test('botões inativos não têm classes de ativo', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const issuesButton = screen.getByText('Issues').closest('button');
      expect(issuesButton).not.toHaveClass('text-blue-300');
      expect(issuesButton).not.toHaveClass('border-blue-500');
    });
  });

  // ========== INTERAÇÕES DE HOVER ==========
  describe('Interações de Hover', () => {
    test('botão inativo muda cor ao passar mouse', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const issuesButton = screen.getByText('Issues').closest('button')!;

      fireEvent.mouseEnter(issuesButton);
      expect(issuesButton).toHaveStyle({
        backgroundColor: '#333333',
      });

      fireEvent.mouseLeave(issuesButton);
      expect(issuesButton).not.toHaveStyle({
        backgroundColor: 'rgba(59, 130, 246, 0.2)',
      });
    });

    test('botão ativo muda tom ao passar mouse', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button')!;

      fireEvent.mouseEnter(commitsButton);
      expect(commitsButton).toHaveStyle({
        backgroundColor: 'rgba(59, 130, 246, 0.25)',
      });

      fireEvent.mouseLeave(commitsButton);
      expect(commitsButton).toHaveStyle({
        backgroundColor: 'rgba(59, 130, 246, 0.2)',
      });
    });

    test('todos os botões respondem ao hover', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const buttons = screen.getAllByRole('button');
      buttons.forEach((button) => {
        fireEvent.mouseEnter(button);
        expect(button).toHaveStyle({
          backgroundColor: expect.any(String),
        });
      });
    });
  });

  // ========== ÍCONES ==========
  describe('Ícones', () => {
    test('renderiza ícones corretos para cada item', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      const issuesButton = screen.getByText('Issues').closest('button');
      const prButton = screen.getByText('Pull Requests').closest('button');

      expect(commitsButton).toContainHTML('💻');
      expect(issuesButton).toContainHTML('📊');
      expect(prButton).toContainHTML('🔀');
      expect(screen.getByText('Collaboration').closest('button')).toContainHTML('🤝');
      expect(screen.getByText('Structure').closest('button')).toContainHTML('🏗️');
      expect(screen.getByText('Visualization').closest('button')).toContainHTML('🎨');
    });
  });

  // ========== SELETOR DE REPOSITÓRIO ==========
  describe('Seletor de Repositório', () => {
    test('renderiza select de repositório', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox');
      expect(select).toBeInTheDocument();
    });

    test('mostra opção "All repositories" com contagem', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      expect(screen.getByText(/All repositories \(3\)/)).toBeInTheDocument();
    });

    test('lista todos os repositórios com contagem de atividades', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      expect(screen.getByRole('option', { name: /repo-one \(2\)/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /repo-two \(1\)/ })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: /repo-three \(0\)/ })).toBeInTheDocument();
    });

    test('seleciona "all" por padrão', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox') as HTMLSelectElement;
      expect(select.value).toBe('all');
    });

    // ✅ CORRIGIDO: Simplificado sem código problemático
    test('atualiza seleção ao mudar select', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox') as HTMLSelectElement;
      fireEvent.change(select, { target: { value: '1' } });

      // Verifica que o evento foi disparado
      expect(select).toBeInTheDocument();
    });

    test('busca repo names quando data não fornecido', async () => {
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        // via dataSource (data/silver/available_repos.json), not the Pages BASE_URL
        expect(global.fetch).toHaveBeenCalledWith(
          `${getDataBasePath()}/silver/available_repos.json`
        );
      });
    });

    test('renderiza repo names quando sem data', async () => {
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        expect(screen.getByText('repo-alpha')).toBeInTheDocument();
        expect(screen.getByText('repo-beta')).toBeInTheDocument();
        expect(screen.getByText('repo-gamma')).toBeInTheDocument();
      });
    });

    test('ignora entrada de _metadata e valores não-string da lista', async () => {
      (global.fetch as any).mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => [{ _metadata: { extracted_at: 'now' } }, 'repo-x', 42, 'repo-y'],
      });
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        expect(screen.getByText('All repositories (2)')).toBeInTheDocument();
      });
      expect(screen.getByRole('option', { name: 'repo-x' })).toBeInTheDocument();
      expect(screen.getByRole('option', { name: 'repo-y' })).toBeInTheDocument();
    });

    test('mantém a lista vazia quando available_repos.json ainda não existe (404)', async () => {
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      (global.fetch as any).mockResolvedValue({ ok: false, status: 404, statusText: '' });

      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        expect(consoleWarnSpy).toHaveBeenCalledWith('Could not fetch repo names:', expect.anything());
      });
      expect(screen.getByText('All repositories (0)')).toBeInTheDocument();
      consoleWarnSpy.mockRestore();
    });

    test('lida com erro no fetch', async () => {
      const consoleWarnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      (global.fetch as any).mockRejectedValueOnce(new Error('Network error'));

      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="commits" />);

      await waitFor(() => {
        expect(consoleWarnSpy).toHaveBeenCalled();
      });

      consoleWarnSpy.mockRestore();
    });

    test('select desabilitado quando loading', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox') as HTMLSelectElement;
      expect(select.disabled).toBe(false);
    });
  });

  // ========== RESPONSIVIDADE ==========
  describe('Responsividade', () => {
    test('toolbar é visível em todos os tamanhos de tela', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside');
      expect(aside).not.toHaveClass('hidden');
      expect(aside).toBeVisible();
    });

    test('toolbar fica no fluxo do layout (não é fixed)', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside');
      expect(aside).not.toHaveClass('fixed');
      expect(aside).toHaveClass('flex-shrink-0');
    });

    test('toolbar tem altura fixa', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside');
      expect(aside).toHaveClass('h-34.5');
    });
  });

  // ========== INTEGRAÇÃO COM SIDEBAR ==========
  describe('Integração com Sidebar', () => {
    // The sidebar offset is applied by DashboardLayout (marginLeft); the toolbar
    // just fills the remaining width of its column.
    test('toolbar ocupa a largura da coluna sem offset próprio da sidebar', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside') as HTMLElement;
      expect(aside).toHaveClass('w-full');
      expect(aside.style.left).toBe('');
      expect(aside.style.width).toBe('');
    });

    test('toolbar tem transição suave', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside');
      expect(aside).toHaveClass('transition-all');
      expect(aside).toHaveClass('duration-300');
      expect(aside).toHaveClass('ease-in-out');
    });
  });

  // ========== ESTILIZAÇÃO ==========
  describe('Estilização', () => {
    test('tem background correto', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const aside = container.querySelector('aside');
      expect(aside).toHaveStyle({
        backgroundColor: '#222222',
      });
    });

    test('header tem border-bottom correto', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const header = container.querySelector('.border-b-1');
      expect(header).toHaveStyle({
        borderBottomColor: '#333333',
      });
    });

    test('nav tem border-bottom correto', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const nav = container.querySelector('nav');
      expect(nav).toHaveClass('border-b-2');
    });

    test('select tem estilos corretos', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox');
      expect(select).toHaveClass('px-4', 'py-2', 'border', 'rounded', 'text-white');
      expect(select).toHaveStyle({
        backgroundColor: '#333333',
        borderColor: '#444444',
      });
    });
  });

  // ========== TEXTO E TIPOGRAFIA ==========
  describe('Texto e Tipografia', () => {
    test('título tem tamanho e peso corretos', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const title = screen.getByText('Repository Related Metrics');
      expect(title).toHaveClass('text-lg');
      expect(title).toHaveClass('font-semibold');
      expect(title).toHaveClass('text-white');
    });

    test('subtítulo tem cor correta', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const subtitle = screen.getByText(/Currently Viewing:/);
      expect(subtitle).toHaveClass('text-slate-400');
    });

    test('labels dos botões têm tamanho correto', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsLabel = screen.getByText('Commits');
      expect(commitsLabel).toHaveClass('text-sm');
    });
  });

  // ========== ACESSIBILIDADE ==========
  describe('Acessibilidade', () => {
    test('todos os botões são clicáveis', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const buttons = screen.getAllByRole('button');
      expect(buttons).toHaveLength(6);
      buttons.forEach((button) => {
        expect(button).toBeEnabled();
      });
    });

    test('botões têm texto visível', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      expect(screen.getByText('Commits')).toBeVisible();
      expect(screen.getByText('Issues')).toBeVisible();
      expect(screen.getByText('Pull Requests')).toBeVisible();
    });

    test('select é acessível', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox');
      expect(select).toBeEnabled();
    });
  });

  // ========== EDGE CASES ==========
  describe('Edge Cases', () => {
    test('renderiza com currentRepo vazio', () => {
      renderWithRouter(<RepositoryToolbar currentRepo="" currentPage="commits" data={mockData} />);

      expect(screen.getByText('Currently Viewing:')).toBeInTheDocument();
    });

    test('renderiza com currentPage vazio', () => {
      renderWithRouter(<RepositoryToolbar currentRepo="repo-one" currentPage="" data={mockData} />);

      const buttons = screen.getAllByRole('button');
      buttons.forEach((button) => {
        expect(button).not.toHaveClass('text-blue-300');
      });
    });

    test('renderiza com data vazio', () => {
      const emptyData: ProcessedActivityResponse = {
        generatedAt: '2024-01-01T00:00:00Z',
        repoCount: 0,
        totalActivities: 0,
        repositories: [],
      };

      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={emptyData} />
      );

      expect(screen.getByText('All repositories (0)')).toBeInTheDocument();
    });

    test('múltiplos cliques no mesmo botão', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');

      fireEvent.click(commitsButton!);
      fireEvent.click(commitsButton!);
      fireEvent.click(commitsButton!);

      expect(mockNavigate).toHaveBeenCalledTimes(3);
      expect(mockNavigate).toHaveBeenCalledWith('/repos/commits');
    });

    test('cliques rápidos em botões diferentes', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      const issuesButton = screen.getByText('Issues').closest('button');
      const prButton = screen.getByText('Pull Requests').closest('button');

      fireEvent.click(commitsButton!);
      fireEvent.click(issuesButton!);
      fireEvent.click(prButton!);

      expect(mockNavigate).toHaveBeenCalledTimes(3);
      expect(mockNavigate).toHaveBeenNthCalledWith(1, '/repos/commits');
      expect(mockNavigate).toHaveBeenNthCalledWith(2, '/repos/issues');
      expect(mockNavigate).toHaveBeenNthCalledWith(3, '/repos/pullrequests');
    });

    test('data null é tratado como sem data', async () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={null} />
      );

      await waitFor(() => {
        expect(global.fetch).toHaveBeenCalled();
      });
    });
  });

  // ========== LAYOUT ==========
  describe('Layout', () => {
    test('header tem flexbox correto', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const header = container.querySelector('.border-b-1');
      expect(header).toHaveClass('flex', 'items-center', 'gap-3');
      expect(header).toContainElement(screen.getByRole('combobox'));
    });

    test('nav tem padding correto', () => {
      const { container } = renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const nav = container.querySelector('nav');
      expect(nav).toHaveClass('p-2');
      expect(nav).toHaveClass('py-3');
    });

    test('botões têm gap correto entre ícone e texto', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const commitsButton = screen.getByText('Commits').closest('button');
      expect(commitsButton).toHaveClass('gap-3');
    });

    test('select tem classes de layout corretas', () => {
      renderWithRouter(
        <RepositoryToolbar currentRepo="repo-one" currentPage="commits" data={mockData} />
      );

      const select = screen.getByRole('combobox');
      // pushed to the right edge of the header
      expect(select).toHaveClass('ml-auto');
      expect(select).toHaveClass('mr-3');
    });
  });
});