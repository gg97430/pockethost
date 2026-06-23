<script lang="ts">
  import FeatureTab from '$components/FeatureTab.svelte'
  import { PLAN_NAMES } from 'pockethost/common'
  import Avatar from '$src/routes/Navbar/Avatar.svelte'
  import { userStore, userSubscriptionType } from '$util/stores'
</script>

<svelte:head>
  <title>Compte - Gestion PocketBase</title>
</svelte:head>

<FeatureTab title="Compte">
  <div class="account-card">
    <div class="account-card-body">
      <div class="account-profile-row">
        <div class="account-profile">
          <Avatar size={80} class="account-avatar" />
          <a
            href="https://gravatar.com/profile"
            target="_blank"
            rel="noopener noreferrer"
            class="account-btn account-btn--outline"
          >
            <wa-icon name="image"></wa-icon>
            Gravatar
          </a>
        </div>

        <div class="account-details">
          <dl class="account-stats-grid">
            <div class="account-stat">
              <dt>Email</dt>
              <dd>
                <span class="account-stat-value">{$userStore?.email}</span>
                <span class="account-stat-sep">·</span>
                <a href="/account/change-email" class="account-stat-link">Changer l'email</a>
              </dd>
            </div>
            <div class="account-stat">
              <dt>Offre</dt>
              <dd class="account-stat-value account-stat-value--plan">{PLAN_NAMES[$userSubscriptionType]}</dd>
            </div>
            <div class="account-stat account-stat--wide">
              <dt>Instances</dt>
              <dd>
                <span class="account-stat-value">{$userStore?.subscription_quantity}</span>
                <span class="account-stat-muted"> autorisées</span>
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  </div>
</FeatureTab>
