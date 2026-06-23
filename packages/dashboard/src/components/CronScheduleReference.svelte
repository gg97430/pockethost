<script lang="ts">
  import QuickReference from '$components/QuickReference.svelte'
</script>

<QuickReference>
  <p class="mb-3">
    La planification est une expression cron qui définit quand le webhook sera appelé en <strong>heure UTC</strong>. Par
    exemple, <code>0 0 * * *</code> signifie tous les jours à minuit UTC.
  </p>
  <table class="table table-sm w-full">
    <thead>
      <tr>
        <th>Champ</th>
        <th>Valeurs autorisées</th>
        <th>Caractères spéciaux</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><code>Minute</code></td>
        <td>0-59</td>
        <td><code>,</code> <code>-</code> <code>*</code> <code>/</code></td>
      </tr>
      <tr>
        <td><code>Heure</code></td>
        <td>0-23</td>
        <td><code>,</code> <code>-</code> <code>*</code> <code>/</code></td>
      </tr>
      <tr>
        <td><code>Jour du mois</code></td>
        <td>1-31</td>
        <td><code>,</code> <code>-</code> <code>*</code> <code>/</code> <code>?</code> <code>L</code> <code>W</code></td
        >
      </tr>
      <tr>
        <td><code>Mois</code></td>
        <td>1-12 ou JAN-DEC</td>
        <td><code>,</code> <code>-</code> <code>*</code> <code>/</code></td>
      </tr>
      <tr>
        <td><code>Jour de la semaine</code></td>
        <td>0-6 ou SUN-SAT</td>
        <td><code>,</code> <code>-</code> <code>*</code> <code>/</code> <code>?</code> <code>L</code> <code>#</code></td
        >
      </tr>
    </tbody>
  </table>
  <div class="mt-3">
    <strong>Macros courantes :</strong>
    <ul class="list-disc pl-6 mt-1">
      <li><code>@yearly</code>: <code>0 0 1 1 *</code> (une fois par an à minuit, le 1er janvier)</li>
      <li><code>@annually</code>: <code>0 0 1 1 *</code> (identique à <code>@yearly</code>)</li>
      <li><code>@monthly</code>: <code>0 0 1 * *</code> (une fois par mois à minuit, le premier jour)</li>
      <li><code>@weekly</code>: <code>0 0 * * 0</code> (une fois par semaine à minuit, le dimanche)</li>
      <li><code>@daily</code>: <code>0 0 * * *</code> (une fois par jour à minuit)</li>
      <li><code>@midnight</code>: <code>0 0 * * *</code> (identique à <code>@daily</code>)</li>
      <li><code>@hourly</code>: <code>0 * * * *</code> (une fois par heure à la minute 0)</li>
    </ul>
  </div>

  <div class="mt-3">
    <strong>Exemples pratiques :</strong>
    <ul class="list-disc pl-6 mt-1">
      <li><code>0 9 * * 1-5</code> - Chaque jour ouvré à 9 h</li>
      <li><code>0 12 * * 1</code> - Chaque lundi à midi</li>
      <li><code>0 0 1 * *</code> - Premier jour de chaque mois à minuit</li>
      <li><code>0 18 * * 5</code> - Chaque vendredi à 18 h</li>
      <li><code>30 2 * * *</code> - Chaque jour à 2 h 30</li>
      <li><code>0 */6 * * *</code> - Toutes les 6 heures (00:00, 06:00, 12:00, 18:00)</li>
      <li><code>0 0 * * 0</code> - Chaque dimanche à minuit</li>
      <li><code>0 8 15 * *</code> - Le 15 de chaque mois à 8 h</li>
      <li><code>0 0 1 1 *</code> - Jour de l'An à minuit</li>
      <li><code>0 12 * * 0,6</code> - Week-end à midi</li>
    </ul>
  </div>
</QuickReference>
