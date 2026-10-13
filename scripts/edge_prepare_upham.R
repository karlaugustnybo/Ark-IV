# Read the published 100-tree node-dated sample and keep extant mammal crowns.
args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 4) stop('Expected Nexus, author taxonomy, output directory and R library')
.libPaths(c(normalizePath(args[4]), .libPaths()))
library(ape)
trees <- read.nexus(args[1])
taxonomy <- read.csv(args[2], stringsAsFactors=FALSE)
stopifnot(!anyDuplicated(taxonomy$tiplabel), !anyDuplicated(taxonomy$Species_Name))
extant <- taxonomy[taxonomy$extinct. == 0,]
dir.create(args[3], recursive=TRUE, showWarnings=FALSE)
receipts <- data.frame()
for (i in seq_along(trees)) {
  raw <- trees[[i]]
  nonmammal <- setdiff(raw$tip.label, taxonomy$tiplabel)
  if (!identical(nonmammal, '_Anolis_carolinensis')) stop('Unexpected nonmammal/outgroup labels')
  stopifnot(setequal(setdiff(raw$tip.label, nonmammal), taxonomy$tiplabel))
  crown <- keep.tip(raw, extant$tiplabel)
  crown$tip.label <- extant$Species_Name[match(crown$tip.label, extant$tiplabel)]
  crown$root.edge <- NULL
  target <- file.path(args[3], sprintf('%03d.tre', i-1))
  write.tree(crown, file=target, digits=15)
  receipts <- rbind(receipts, data.frame(tree_id=i-1, source_tree_name=names(trees)[i],
      raw_tips=length(raw$tip.label), extant_mammal_tips=length(crown$tip.label),
      excluded_author_extinct=sum(taxonomy$extinct. != 0), excluded_outgroups=length(nonmammal)))
}
write.csv(receipts, file.path(args[3], 'preparation.csv'), row.names=FALSE)
cat('Prepared',length(trees),'extant mammal crowns\n')
