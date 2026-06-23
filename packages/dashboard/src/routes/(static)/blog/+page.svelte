<script lang="ts">
  import { gravatarUrl } from '$lib/gravatar'
  import { getAuthor } from '$lib/blog/authors'
  import { toc } from './toc'
</script>

<div class="blog-index-shell">
  <div class="blog-index-header">
    <h1 class="blog-index-title">Dernières nouvelles de PocketHost</h1>
    <p class="blog-index-lead">
      Suivez les nouvelles fonctionnalités, les améliorations de plateforme et les notes de l'équipe PocketHost.
    </p>
  </div>

  <div class="blog-index-grid">
    {#each toc as item, index}
      {@const author = getAuthor(item.author)}
      <a href={item.path} class="blog-card-link">
        <article class="blog-card">
          <div class="blog-card-body">
            {#if index < 3}
              <span class="blog-card-badge">Nouveau</span>
            {/if}

            <h2 class="blog-card-title">{item.title}</h2>

            {#if item.description}
              <p class="blog-card-description">{item.description}</p>
            {/if}

            <div class="blog-card-footer">
              <div class="blog-card-meta">
                <img src={gravatarUrl(author.email, 20)} alt="" width="20" height="20" />
                <span class="blog-card-meta-handle">{author.handle}</span>
                {#if item.date}
                  <span class="blog-card-meta-sep">·</span>
                  <span>{item.date}</span>
                {/if}
              </div>

              <span class="blog-card-cta">
                Lire la suite
                <wa-icon name="arrow-right"></wa-icon>
              </span>
            </div>
          </div>
        </article>
      </a>
    {/each}
  </div>

  <div class="blog-discord-cta">
    <h3 class="blog-discord-title">Restez informé</h3>
    <p class="blog-discord-lead">
      Rejoignez notre communauté Discord pour recevoir les dernières nouvelles et échanger avec d'autres utilisateurs PocketHost.
    </p>
    <a href="https://discord.gg/nVTxCMEcGT" target="_blank" rel="noopener noreferrer" class="blog-discord-button">
      Rejoindre la communauté Discord
      <wa-icon name="discord" family="brands"></wa-icon>
    </a>
  </div>
</div>
